import time
import os
import re
import warnings
import json
import subprocess
from cmd import Cmd
import xml.etree.ElementTree as ET
from dataclasses import dataclass
import logging
from openai import OpenAI
from typing import List, Dict, Any
from functools import lru_cache

DEEPSEEK_API_KEY='sk-c4e470b3ca36497d87cabd72c79b4fcf'
OPENROUTER_API_KEY='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'
GROQ_API_KEY='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'

OPENROUTER_API_URL='https://openrouter.ai/api/v1'
GROQ_API_URL='https://api.groq.com/openai/v1'
DEEPSEEK_API_URL='https://api.deepseek.com'
# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

@dataclass
class Job:
    """Represents a job in the agent's job queue."""
    id: str
    type: str  # 'bash', 'python', or 'reasoning'
    content: str
    depends_on: List[str] = None
    status: str = 'pending'
    output_ref: str = None  # Variable name to store output in
    model: str = None  # Add model parameter
    response_format: str = None  # Add response format field

class Agent:
    """Manages and executes jobs in a queue."""
    def __init__(self):
        self.job_queue: List[Job] = []
        self.outputs: Dict[str, Any] = {}  # Stores results of completed jobs
        self.output_buffer: List[str] = []  # Global output accumulator
    
    def add_job(self, action_xml: str):
        """Adds jobs to the queue based on an XML action plan."""
        try:
            root = ET.fromstring(action_xml)
        except ET.ParseError as e:
            logger.error(f"Invalid XML: {e}")
            return

        for action in root.findall('action'):
            job_id = action.get('id')
            job_type = action.get('type')
            content_element = action.find('content') or action
            content = content_element.text.strip() if content_element is not None and content_element.text else None

            if not content:
                logger.error(f"Action {job_id} missing content")
                continue

            depends_on = list({d for d in (action.get('depends_on', '').split(',') + re.findall(r'\{\{outputs\.([\w]+)[^\}]*\}\}', content)) if d})

            model = action.get('model') if job_type == 'reasoning' else None
            response_format = action.get('format') if job_type == 'reasoning' else None

            if job_type != 'reasoning' and (action.get('model') or action.get('format')):
                logger.warning(f"Ignoring model/format on {job_type} job {job_id}")

            self.job_queue.append(Job(id=job_id, type=job_type, content=content, depends_on=depends_on, model=model, response_format=response_format))

    def _execute_reasoning_subjob(self, query: str, parent_id: str) -> str:
        """Handles nested model calls from Python scripts."""
        subjob_id = f"{parent_id}_sub"
        self.add_job(f"""<actions><action id="{subjob_id}" type="reasoning"><content>{query}</content></action></actions>""")
        self.process_queue()
        return self.outputs.get(subjob_id, {}).get('raw_response', 'No response')

    def _execute_bash_job(self, job: Job):
        """Executes a bash job."""
        env_vars = os.environ.copy()
        env_vars.update({f'OUTPUT_{dep_id}': str(self.outputs.get(dep_id)) for dep_id in job.depends_on})
        env_vars['PS1'] = ''  # Cleaner output

        try:
            result = subprocess.run(job.content, shell=True, capture_output=True, text=True, env=env_vars, timeout=60)  # Add timeout
            output = result.stderr if result.returncode != 0 else result.stdout
            clean_output = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', output).strip()
            clean_output = f"[Exit {result.returncode}] {clean_output}"

            self.outputs[job.id] = {'raw_response': clean_output, 'output': clean_output, 'status': 'completed'}
            self.output_buffer.append(clean_output)
        except subprocess.TimeoutExpired:
            error_msg = "Bash job timed out after 60 seconds."
            self.outputs[job.id] = {'error': {'error_msg': error_msg}, 'output': error_msg, 'status': 'failed'}
            logger.error(error_msg)
        except Exception as e:
            error_msg = str(e)
            self.outputs[job.id] = {'error': {'error_msg': error_msg}, 'output': f"Bash Error: {error_msg}", 'status': 'failed'}
            logger.exception(f"Bash job failed: {error_msg}")

    
    def _execute_python_job(self, job: Job):
        """Executes a python job."""
        import sys
        from io import StringIO
        old_stdout = sys.stdout
        sys.stdout = buffer = StringIO()

        try:
            locs = {
                'outputs': {dep: self.outputs.get(dep) for dep in job.depends_on},
                'agent': self,
                'model_call': lambda query: self._execute_reasoning_subjob(query, job.id),
                'get_output': lambda key: self.outputs.get(key),
                'json': json,
                'append_output': lambda text: self.output_buffer.append(str(text)),
                'validate_json': lambda data, keys: all(k in data for k in keys),
                'get_json_field': lambda job_id, field: self.outputs.get(job_id, {}).get('response_json', {}).get('content', {}).get(field)
            }
            exec(job.content, globals(), locs)
            output = buffer.getvalue()
            self.output_buffer.append(output)
            self.outputs[job.id] = {'raw_response': output, 'output': output, 'variables': locs}
        except Exception as e:
            error_msg = str(e)
            if "response_json" in error_msg:
                error_msg += "\n🔍 JSON ISSUE - Possible fixes:" \
                           "\n1. Verify previous job uses format=\"json\"" \
                           "\n2. Check outputs[\"ID\"][\"json_error\"] for parsing issues" \
                           "\n3. Access fields via outputs[\"ID\"][\"response_json\"][\"content\"]"

            missing_deps = [d for d in job.depends_on if d not in self.outputs]
            if missing_deps:
                error_msg += f"\n🔗 MISSING DEPENDENCIES: {', '.join(missing_deps)}"
            output = f"Python Error: {error_msg}"
            self.outputs[job.id] = {'error': {'error_msg': output}, 'output': output, 'status': 'failed'}
            logger.exception(f"Python job failed: {error_msg}")  # Correctly log the exception

        finally:
            sys.stdout = old_stdout

    @lru_cache(maxsize=32)
    def _execute_reasoning_job(self, job: Job):
        """Executes a reasoning job using OpenAI API through OpenRouter."""
        logger.info(f"[🤖] Running reasoning job '{job.id}' with model {job.model}")

        if not OPENROUTER_API_KEY:
            raise ValueError("OPENROUTER_API_KEY environment variable not set")
        
        client = OpenAI(api_key=OPENROUTER_API_KEY, base_url=OPENROUTER_API_URL)
        
        substitutions = {
            dep_id: str(self.outputs.get(dep_id, {}).get('raw_response', f'MISSING_OUTPUT_FOR_{dep_id}'))
            for dep_id in job.depends_on
        }
        processed_content = re.sub(r'\{\{outputs\.(\w+)(\.raw_response)?\}\}', lambda m: substitutions.get(m.group(1), 'MISSING_OUTPUT'), job.content)

        # Determine system message based on job type
        if job.id == "0" or job.id == 'initial-thought':  # Initial planning job
            system_msg = """You are an AI planner. Generate XML action plans with these guidelines:

CORE PRINCIPLES:
1. Prefer simple, linear workflows unless complexity is required
2. Use appropriate formats for data interchange:
   - JSON when Python code needs structured data
   - Raw text for human-readable outputs
3. Ensure clear dependency declarations

JSON USAGE GUIDELINES (use only when needed):
- Consider format="json" when:
  * Output requires specific field names
  * Data will be processed programmatically
  * Exact structure validation is critical
  * if a script depends on a reasoning job, that reasoning job MUST be of type JSON
  * ensure JSON content includes only necessary properties

DATA FLOW RULES:
- All data MUST flow through declared dependencies
- Access outputs through:
  * Bash: $OUTPUT_ID
  * Python: outputs["ID"]["raw_response"] 
  * Reasoning: {{outputs.ID.raw_response}}

Actions can specify models:
   - google/gemini-2.0-flash-001: reasoning, largest context window for long document polishing
   - openai/gpt-4o: best at trivia and general knowledge 
   - openai/o1-mini: fast general reasoning 
   - anthropic/claude-3.5-sonnet: creative writing and poetry

When asked to produce a document, use the reasoning model to generate an outline 
    - following steps can reference these outlines to fill them in piece by piece
    - Prioritize making multiple calls when asked to generate long form content. Aim for chunks of 1000-2000 words maximum
    - Ensure steps conform to defined data access patterns -- semantic requests for data will not be fulfilled

PLAN EXAMPLES:
<action type="reasoning" id="analysis" model="openai/o1-mini-2024-09-12">
  <content>Generate market analysis report</content>
</action>

<action type="reasoning" id="structured_analysis" model="anthropic/claude-3-haiku" format="json">
  <content>Return JSON with { "trends": [], "summary": "" }</content>
</action>

<action type="python" id="process" depends_on="structured_analysis">
  <content>
try:
    data = outputs["structured_analysis"]["response_json"]["content"]
    print(f"Found {len(data['trends'])} trends")
  </content>
</action>"""

            # Create messages array with examples
            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": "Generate an XML action plan to: find the current president"},
                {"role": "assistant", "content": """<?xml version="1.0"?>
<actions>
  <action type="bash" id="1" depends_on="2">
    <content>pip install wikipedia</content>
  </action>
  <action type="python" id="2">
    <content>
import wikipedia
try:
    president_page = wikipedia.page("President of the United States")
    print(president_page.content)
except wikipedia.exceptions.PageError:
    print("Error: Wikipedia page not found.")
except wikipedia.exceptions.DisambiguationError as e:
    print(f"Error: {e.options}")
    </content>
  </action>
  <action type="reasoning" id="3" model="google/gemini-2.0-flash-001" depends_on="1">
    <content>Based on {{outputs.1.raw_response}}, identify current president.</content>
  </action>
</actions>"""},
                {"role": "user", "content": "Create a technical document outline with analysis"},
                {"role": "assistant", "content": """<?xml version="1.0"?>
<actions>
  <action type="reasoning" id="plan" model="deepseek/deepseek-r1" depends_on="priority" format="json">
    <content>Create outline focused on technical aspects...</content>
  </action>
  <action type="reasoning" id="1" model="anthropic/claude-3.5-sonnet" depends_on="plan">
    <content>Expand {{outputs.plan.raw_response}} into detailed analysis...</content>
  </action>
  <action type="python" id="2" depends_on="plan">
    <content>
try:
    data = outputs["plan"]["response_json"]
    print(f"Processed result: {data['content']}")
except Exception as e:
    print(f"Error processing output: {str(e)}")
    </content>
  </action>
</actions>"""},
                {"role": "user", "content": "go to google news and summarize it for me"},
                {"role": "assistant", "content": """<?xml version="1.0"?>
<actions>
  <action type="python" id="1" model="google/gemini-2.0-flash-001">
    <content>
import requests
from bs4 import BeautifulSoup

try:
    url = "https://news.google.com/news/rss"
    response = requests.get(url)
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "xml")
    items = soup.find_all("item")

    news_items = []
    for item in items:
        title = item.find("title").text
        link = item.find("link").text
        description = item.find("description").text
        news_items.append({"title": title, "link": link, "description": description})

    print(news_items)

except requests.exceptions.RequestException as e:
    print(f"Request Error: {e}")
except Exception as e:
    print(f"An error occurred: {e}")
</content>
  </action>
  <action type="reasoning" id="2" model="google/gemini-2.0-flash-001" depends_on="1">
    <content>Summarize the following news articles: {{outputs.1.raw_response}}</content>
  </action>
</actions>"""},
                {"role": "user", "content": processed_content}
            ]
        else:  # Subsequent reasoning queries
            system_msg = """You are a valuable part of a content production pipeline. Please produce the content specified with ZERO editorialization. Given any specifications (style, length, formatting) you must match them exactly. If asked to stitch together and format parts, do not leave out a single sentence from the original. NEVER produce incomplete content -- prioritizing ending neatly before tokens run out."""
            messages = [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": processed_content}
            ]

        # Use job-specific model if specified, else google/gemini-2.0-flash-001
        model = job.model or 'google/gemini-2.0-flash-001'

        # Prepare API parameters
        api_params = {
            "model": model,
            "messages": messages,
            "stream": False
        }

        if job.response_format == 'json':
            api_params["response_format"] = {"type": "json_object"}
            # Update system message for JSON responses
            system_msg = """You MUST return valid JSON:
- Be CONCISE - trim all unnecessary fields/variables                                                                                                                                                                                               
- Summarize lengthy content instead of verbatim inclusion                                                                                                                                                                                          
- Use short property names where possible                                                                                                                                                                                                          
- If content exceeds 200 characters, provide a summary    
- Escape special characters
- No markdown code blocks
- Include ALL data fields"""
            messages[0]["content"] = system_msg

        try:
            response = client.chat.completions.create(**api_params)
            response_content = response.choices[0].message.content

            if not response_content:
                raise ValueError("Empty response from API - check model permissions/availability")
            
            if response.choices[0].finish_reason != 'stop':
                logger.warning(f"\n⚠️ API WARNING: Finish reason '{response.choices[0].finish_reason}'")
                logger.warning(f"   Token Usage: {response.usage}")
                
            # Store response with type information
            output_data = {
                'raw_response': response_content,
                'response_type': 'actions' if '<actions>' in response_content else 'content'
            }
            
            if job.response_format == 'json':
                output_data['response_json'] = {}  # Initialize even if parsing fails
                try:
                    parsed_json = json.loads(response_content)
                    output_data['response_json'] = parsed_json
                except json.JSONDecodeError as e:
                    output_data['json_error'] = f"Invalid JSON: {str(e)}"
                    logger.error(f"JSON decode error: {e}")
                
            self.outputs[job.id] = output_data
            self.output_buffer.append(response_content)

        except Exception as e:
            logger.exception(f"Reasoning job failed: {e}")  # Correctly log the exception

            error_details = {
                'job_id': job.id,
                'job_type': job.type,
                'error_type': type(e).__name__,
                'error_msg': str(e),
                'dependencies': job.depends_on,
                'missing_deps': [d for d in job.depends_on if d not in self.outputs],
                'content_snippet': job.content[:200] + ('...' if len(job.content) > 200 else '')
            }
            if job.type == 'reasoning':
                error_details['model'] = job.model
                error_details['response_format'] = job.response_format        
            self.outputs[job.id] = {'error': error_details, 'status': 'failed', 'timestamp': time.time()}
            raise # Reraise exception
                
    def process_queue(self):
        """Processes pending jobs in the queue."""
        for job in list(self.job_queue):
            if job.status != 'pending':
                continue
            if not all(self.outputs.get(dep) not in (None, 'No response') for dep in job.depends_on):
                continue
            try:
                if job.type == 'bash':
                    self._execute_bash_job(job)
                elif job.type == 'python':
                    self._execute_python_job(job)
                elif job.type == 'reasoning':
                    self._execute_reasoning_job(job)
                job.status = 'completed'
            except Exception as e:
                job.status = f'failed: {type(e).__name__}'
                logger.error(f'Job {job.id} failed with {e}')
                self.handle_job_failure(job, e)

    def handle_job_failure(self, job: Job, error: Exception):
        """Handles failed jobs, logs details and sets appropriate status."""
        error_details = {
            'job_id': job.id,
            'job_type': job.type,
            'error_type': type(error).__name__,
            'error_msg': str(error),
            'dependencies': job.depends_on,
            'missing_deps': [d for d in job.depends_on if d not in self.outputs],
            'content_snippet': job.content[:200] + ('...' if len(job.content) > 200 else '')
        }

        if job.type == 'bash':
            error_details['command'] = job.content.split('\n')[0][:100]
        elif job.type == 'python':
            import traceback
            error_details['traceback'] = traceback.format_exc()
        elif job.type == 'reasoning':
            error_details['model'] = job.model
            error_details['response_format'] = job.response_format
        
        # Rich error message
        error_lines = [
            "\n⚡️🔥 JOB FAILURE ANALYSIS 🔥⚡️",
            f"🧩 Job ID:    {job.id} ({job.type.upper()})",
            f"📛 Error Type: {error_details['error_type']}",
            f"📝 Message:    {error_details['error_msg']}",
        ]
        
        if job.type == 'bash':
            error_lines.extend([
                "💻 Command Fragment:",
                f"   {error_details['command']}"
            ])
        elif job.type == 'python':
            error_lines.extend([
                "🐍 Python Traceback:",
                *[f"   {line}" for line in error_details['traceback'].split('\n') if line],
                "💻 Code Fragment:",
                *[f"   {line}" for line in error_details['content_snippet'].split('\n')[:3]]
            ])
        elif job.type == 'reasoning':
            error_lines.extend([
                f"🧠 Model: {job.model}",
                f"📤 Response Format: {job.response_format or 'text'}"
            ])
        
        if error_details['missing_deps']:
            error_lines.extend([
                "🔗 Missing Dependencies:",
                *[f"   - {dep}" for dep in error_details['missing_deps']]
            ])
        
        if job.type == 'reasoning' and job.response_format == 'json':
            error_lines.extend([
                "📌 JSON Validation Tips:",
                "1. Check for trailing commas in objects/arrays",
                "2. Ensure all strings are properly quoted",
                "3. Verify response matches the exact schema requested"
            ])
        
        error_lines.append("="*50)
        print('\n'.join(error_lines))

        self.outputs[job.id] = {'error': error_details, 'status': 'failed', 'timestamp': time.time()}

class AgentCLI(Cmd):
    """Command-line interface for the Agent."""
    prompt = 'agent> '
    
    def __init__(self):
        super().__init__()
        self.agent = Agent()
        self.initial_query = None
        self.feedback_history: List[str] = []
        self.last_generated_plan_xml = None
    
    def default(self, line: str):
        """Handles natural language queries."""
        if line.strip().lower() == 'exit':
            return self.do_exit('')
        self._process_query(line)

    def _process_query(self, query:str):
        """Processes a user query by creating and executing an initial planning job."""
        self.agent = Agent()
        self.initial_query = query
        self.agent.add_job(f"""<actions><action id="0" type="reasoning"><content>Generate an XML action plan to: {query} Requirements: - Ensure any Python code is properly formatted with dependencies installed - Pass data between jobs appropriately - If a Python job depends on reasoning output, that reasoning job must use format="json" - Break long-form content into manageable chunks</content></action></actions>""")
        self.agent.process_queue()
        self._handle_response()
    
    def _handle_response(self):
        """Processes and displays the agent's response to a query."""
        if not DEEPSEEK_API_KEY:
            print("\n❌ Missing DEEPSEEK_API_KEY environment variable.\nGet an API key from https://platform.deepseek.com and run: export DEEPSEEK_API_KEY=your_key_here")
            return
                    
        initial_job = next((job for job in self.agent.job_queue if job.id == "0"), None)
            
        if initial_job and initial_job.status.startswith('failed'):
            print(f"\n❌ Initial processing failed: {initial_job.status.split(':', 1)[-1].strip()}")
            return
        
        last_output = self.agent.outputs.get("0")
        
        if not last_output:
            print("\n🔍 No response received - Possible API issues or empty response.\nCheck your DEEPSEEK_API_KEY environment variable")
            return
                
        response_content = last_output['raw_response']
        response_type = last_output.get('response_type')

        if response_type == 'actions' or '<actions>' in response_content:
            try:
                response_content = response_content.removeprefix('```xml').removesuffix('```').strip()
                try:
                    root = ET.fromstring(response_content)
                except ET.ParseError as e:
                    print(f"\n❌ Invalid XML structure: {e}.\nCommon fixes:\n1. Remove markdown code fences (```xml)\n2. Ensure proper tag nesting\n3. Escape special characters like & with &amp;")
                    return

                self.last_generated_plan_xml = response_content

                inputs = {}
                for req in root.findall('request_input'):
                    input_id = req.get('id')
                    prompt_text = (req.get('desc') or req.text or "Please provide input:") + " "
                    inputs[input_id] = input(prompt_text)
                self.agent.outputs.update(inputs)
                
                action_xml = ET.tostring(root, encoding='unicode')
                self.agent.add_job(action_xml)
                
                print("\n" + "="*50 + "\n📋 Generated Plan\n" + "="*50)
                for job in self.agent.job_queue:
                    if job.status == 'pending' and job.id != "0":
                        icon = "🖥️" if job.type == 'bash' else "🐍" if job.type == 'python' else "💭"
                        deps = f"Deps: {', '.join(job.depends_on) or 'none'}"
                        model = f"Model: {job.model}{' [JSON]' if job.response_format == 'json' else ''}" if job.type == 'reasoning' else ""
                        
                        print(f"{icon} [{job.id}] {job.type.upper()} {model}")
                        print(f"   └─ Dependencies: {deps}")
                        print("      Content:")
                        for line in job.content.split('\n'):
                            print(f"      │ {line}")
                        print("─"*50)
                user_input = input("\n🚀 Options: (y) Execute, (n) Cancel, (R) Regenerate, (!) Add feedback: ").lower()
                feedback = None
                if user_input.startswith('!'):
                    feedback = user_input[1:].strip()
                    print(f"📝 Feedback noted: {feedback}")
                    user_input = 'r'
                    
                if user_input == 'y':
                    print("\n[Executing Generated Plan]")
                    self.agent.process_queue()
                    self._show_results()
                elif user_input == 'r':
                    print("\n🔄 Regenerating plan with feedback...")
                    original_query = self.initial_query
                    if feedback:
                        self.feedback_history.append(feedback)
                    feedback_clause = ""
                    if self.feedback_history:
                        feedback_clause = "\n\nFeedback from previous plans:\n- " + "\n- ".join(self.feedback_history)

                    self.agent = Agent()
                    self.agent.add_job(f"""<actions><action id="0" type="reasoning"><content>Generate an XML action plan to: {original_query}{feedback_clause}</content></action></actions>""")
                    self.agent.process_queue()
                    self._handle_response()
                else:
                    print("Execution canceled")
                    self.agent.job_queue = []
                    
            except ET.ParseError:
                print("\n[Final Answer]")
                print(response_content)
            return

        print("\n[Assistant Response]")
        try:
            wrapped = f"<wrapper>{response_content}</wrapper>"
            root = ET.fromstring(wrapped)
            response_node = root.find('.//response')
            if response_node is not None and response_node.text:
                print(response_node.text.strip())
            else:
                print("Received empty response")
        except ET.ParseError:
            print(response_content)

    def _show_results(self):
        """Displays the results of the executed jobs."""
        print("\n" + "="*50 + "\n📊 Execution Results\n" + "="*50)
        for job in self.agent.job_queue:
            if job.id == "0":
                continue
                
            result = self.agent.outputs.get(job.id, {})
            status_icon = "✅" if job.status == 'completed' else "❌"
            icon = "🖥️" if job.type == 'bash' else "🐍" if job.type == 'python' else "💭"
            model = f"Model: {job.model}{' [JSON]' if job.response_format == 'json' else ''}" if job.model else ""
            output = result.get('error', {}).get('error_msg') or result.get('output') or result.get('raw_response') or str(result)
            output_preview = (output[:80] + "...") if len(output) > 80 else output
            
            print(f"{status_icon} {icon} [{job.id}] {job.type.upper()} {model}")
            print(f"   └─ {output_preview}")

        while True:
            print("\nPost-execution options:")
            print("  q - Rerun original query")
            print("  p - Rerun last plan")
            print("  f - Add feedback & regenerate")
            print("  x - Show generated XML plan")
            print("  exit - Return to prompt")
            choice = input("agent(post)> ").lower()
        
            if choice == 'q':
                self._process_query(self.initial_query)
                break
            elif choice == 'p':
                if self.last_generated_plan_xml:
                    print("\n🔄 Re-running last plan...")
                    self.agent = Agent()
                    self.agent.add_job(self.last_generated_plan_xml)
                    self.agent.process_queue()
                    self._show_results()
                else:
                    print("No plan stored to rerun")
                break
            elif choice == 'f':
                feedback = input("Enter feedback: ").strip()
                if feedback:
                    self.feedback_history.append(feedback)
                    print("\n🔄 Regenerating with feedback...")
                    feedback_clause = "\n\nFeedback history:\n- " + "\n- ".join(self.feedback_history)
                    self.agent = Agent()
                    self.agent.add_job(f"""<actions><action id="0" type="reasoning"><content>Generate an XML action plan to: {self.initial_query}{feedback_clause} Previous Plan: {self.last_generated_plan_xml} Don't change anything unless the user has specifically requested it.</content></action></actions>""")
                    self.agent.process_queue()
                    self._handle_response()
                    break        
            elif choice == 'x':
                if self.last_generated_plan_xml:
                    print("\n📦 Stored XML Plan:\n" + "="*50)
                    print(self.last_generated_plan_xml)
                    print("="*50)
                else:
                    print("No XML plan stored")
            elif choice == 'exit':
                break
            else:
                print("Invalid option")

        self._reset_state()

    def _reset_state(self):
        """Resets agent and query-related state."""
        self.agent.job_queue = []
        self.agent.outputs = {}
        self.initial_query = None

    def onecmd(self, line):
        """Override to handle natural language inputs properly"""
        if not line:
            return False
        if line.split()[0].lower() not in ['exit']:
            return self.default(line)
        return super().onecmd(line)
        
    def do_exit(self, arg):
        """Exit the CLI"""
        return True

if __name__ == '__main__':
    AgentCLI().cmdloop()