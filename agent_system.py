from dataclasses import dataclass
import xml.etree.ElementTree as ET
import subprocess
import os
import re
import warnings
from cmd import Cmd
from openai import OpenAI

DEEPSEEK_API_KEY='sk-c4e470b3ca36497d87cabd72c79b4fcf'
OPENROUTER_API_KEY='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'
GROQ_API_KEY='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'

OPENROUTER_API_URL='https://openrouter.ai/api/v1'
GROQ_API_URL='https://api.groq.com/openai/v1'
DEEPSEEK_API_URL='https://api.deepseek.com'

@dataclass
class Job:
    id: str
    type: str  # 'bash', 'python', or 'reasoning'
    content: str
    depends_on: list[str] = None
    status: str = 'pending'
    output_ref: str = None  # Variable name to store output in
    model: str = None  # Add model parameter
    response_format: str = None  # Add response format field

class Agent:
    def __init__(self):
        self.job_queue = []
        self.outputs = {}  # Stores results of completed jobs
        self.output_buffer = []  # Global output accumulator
        
    def add_job(self, action_xml: str):
        root = ET.fromstring(action_xml)
        for action in root.findall('action'):
            job_id = action.get('id')
            job_type = action.get('type')
            
            # Modified content extraction logic
            content_element = action.find('content')
            if content_element is None:  # Handle direct text content
                content_element = action
                
            if content_element is None or content_element.text is None:
                raise ValueError(f"Action {job_id} missing content element")
                
            content = content_element.text.strip()
            depends_on = action.get('depends_on', '').split(',') if action.get('depends_on') else []
            
            # Add implicit dependencies from template variables
            template_deps = re.findall(r'\{\{outputs\.(\w+)\}\}', content)  # Allow alphanumeric IDs
            depends_on += template_deps
            
            # Remove duplicates and empty strings
            depends_on = list(set([d for d in depends_on if d]))
            
            # Add model parameter extraction
            model = action.get('model')  # Get model if specified
            if job_type == 'reasoning' and not model:
                model = 'google/gemini-2.0-flash-001'  # Default for reasoning

            # Add format parameter extraction
            response_format = action.get('format')
            self.job_queue.append(Job(
                id=job_id,
                type=job_type,
                content=content,
                depends_on=depends_on,
                model=model,  # Add model parameter
                response_format=response_format  # Store response format
            ))

    def _execute_reasoning_subjob(self, query, parent_id):
        """Handle nested model calls from Python scripts"""
        subjob_id = f"{parent_id}_sub"
        self.add_job(f"""<actions>
            <action id="{subjob_id}" type="reasoning">
                <content>{query}</content>
            </action>
        </actions>""")
        self.process_queue()
        return self.outputs.get(subjob_id, {}).get('raw_response', 'No response')

    def process_queue(self):
        # Process a copy to safely modify original list
        for job in list(self.job_queue):  # Iterate copy of queue
            if job.status != 'pending':
                continue
            if all(self.outputs.get(dep) not in (None, 'No response') for dep in job.depends_on):
                try:
                    if job.type == 'bash':
                        # Inject dependency outputs as environment variables
                        env_vars = os.environ.copy()
                        env_vars.update({f'OUTPUT_{dep_id}': str(self.outputs.get(dep_id)) 
                                    for dep_id in job.depends_on})
                        env_vars['PS1'] = ''  # Cleaner output
                        
                        result = subprocess.run(
                            job.content, 
                            shell=True, 
                            capture_output=True, 
                            text=True,
                            env=env_vars
                        )
                        # Clean ANSI escape codes
                        clean_output = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', result.stdout)
                        clean_output = clean_output.strip()
                        self.outputs[job.id] = clean_output
                        self.output_buffer.append(clean_output)
                    elif job.type == 'python':
                        # Capture printed output
                        import sys
                        from io import StringIO
                        old_stdout = sys.stdout
                        sys.stdout = buffer = StringIO()
                        
                        try:
                            import json  # Ensure json is available
                            locs = {
                                'outputs': {dep: self.outputs.get(dep) for dep in job.depends_on},
                                'agent': self,  # Add agent reference
                                'model_call': lambda query: self._execute_reasoning_subjob(query, job.id),
                                'get_output': lambda key: self.outputs.get(key),
                                'json': json,  # Make json available to scripts
                                'append_output': lambda text: self.output_buffer.append(str(text))
                            }
                            exec(job.content, globals(), locs)
                            output = buffer.getvalue()
                            self.output_buffer.append(output)
                            # Store output with raw_response for consistent access
                            self.outputs[job.id] = {
                                'raw_response': output,  # Add raw_response field
                                'output': output,
                                'variables': locs
                            }
                        except Exception as e:
                            error_msg = str(e)
                            if "KeyError" in error_msg:
                                error_msg += "\n🔑 Missing dependency - Verify:"
                                error_msg += "\n1. All referenced jobs are in depends_on"
                                error_msg += "\n2. Previous jobs completed successfully"
                                error_msg += "\n3. Python scripts use outputs['ID']['raw_response']"
                            output = f"Python Error: {error_msg}"
                            self.outputs[job.id] = {
                                'error': output,
                                'output': output  # Maintain output key for compatibility
                            }
                        finally:
                            sys.stdout = old_stdout
                    elif job.type == 'reasoning':
                        # Add query logging
                        print(f"\n[Reasoning Query: {job.model}]\n{job.content}\n{'='*50}")

                        api_key = OPENROUTER_API_KEY
                        if not api_key:
                            raise ValueError("DEEPSEEK_API_KEY environment variable not set")
                            
                        client = OpenAI(
                            api_key=OPENROUTER_API_KEY,
                            base_url=OPENROUTER_API_URL,
                        )
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            # Handle {{outputs.ID}} and {{outputs.ID.raw_response}} patterns
                            substitutions = {
                                dep_id: str(self.outputs.get(dep_id, {}).get('raw_response', f'MISSING_OUTPUT_FOR_{dep_id}'))
                                for dep_id in job.depends_on
                            }
                            processed_content = re.sub(
                                r'\{\{outputs\.(\w+)(\.raw_response)?\}\}',
                                lambda m: substitutions.get(m.group(1), 'MISSING_OUTPUT'),
                                job.content
                            )

                            # Determine system message based on job type
                            if job.id == "0":  # Initial planning job
                                system_msg = """You are an AI planner. Generate XML action plans with these requirements:
1. Actions can specify models:
   - google/gemini-2.0-flash-001: reasoning, largest context window for long document polishing
   - openai/gpt-4o: best at trivia and general knowledge 
   - openai/o1-mini: fast general reasoning 
    - anthropic/claude-3.5-sonnet: creative writing and poetry
2. Strict XML Formatting:
   - NEVER use markdown code blocks (```xml) 
   - ALWAYS start with <?xml version="1.0"?> as first line
   - Remove ALL markdown formatting from XML
   - Ensure proper XML escaping for special characters
   - Validate XML structure before responding
3. XML Structure:
   - Start with <?xml version="1.0"?>
   - Wrap ALL steps in <actions> tags
   - Required tags:
     * <actions>: Container for all steps
     * <action type="TYPE" id="ID" model="MODEL">: Single step
     * <content>: Contains instructions/code
   - Action types: "bash", "python", "reasoning"
4. Strict Data Passing Rules:
   - ALL data MUST flow through declared dependencies
   - Access outputs ONLY through approved methods:
     * Python: outputs["ID"]["raw_response"] 
     * Bash: $OUTPUT_ID
     * Reasoning: {{outputs.ID.raw_response}}
   - Plans WILL FAIL if data is passed through:
     * Implicit execution order
     * Undeclared dependencies
     * Direct variable access
5. JSON Format Actions:
   - Add format="json" to action tag
   - Specify required JSON structure in content
   - Response must match structure exactly
   - ALWAYS use this to pipe reasoning outputs into python steps
   - NEVER ask a normal reasoning job for JSON data
6. When asked to produce a document, use the reasoning model to generate an outline 
    - following steps can reference these outlines to fill them in piece by piece
    - Prioritize making multiple calls when asked to generate long form content. Aim for chunks of 1000-2000 words maximum
    - Ensure steps conform to defined data access patterns -- semantic requests for data will not be fulfilled
    """

                                # Create messages array with examples
                                messages = [
                                    {"role": "system", "content": system_msg},
                                    {"role": "user", "content": "Generate an XML action plan to: find the current president"},
                                    {"role": "assistant", "content": """<?xml version="1.0"?>
<actions>
  <action type="bash" id="1" depends_on="2">
    <content>pip install wikipedia</content>
  </action>
  <action type="python" id="2" model="google/gemini-2.0-flash-001">
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
  <action type="python" id="2" depends_on="1">
    <content>
import json
try:
    data = json.loads(outputs["1"]["raw_response"])
    print(f"Processed result: {data['analysis']}")
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

                            # Use job-specific model if specified, else deepseek-r1
                            # Force deepseek-r1 for initial planning job
                            model = job.model or 'google/gemini-2.0-flash-001'
                            # model = 'google/gemini-2.0-flash-001'
                            # Prepare API parameters
                            api_params = {
                                "model": model,
                                "messages": messages,
                                "max_tokens": 4096,
                                "stream": False
                            }

                            if job.response_format == 'json':
                                api_params["response_format"] = {"type": "json_object"}
                                # Update system message for JSON responses
                                system_msg = """You MUST return valid JSON matching EXACTLY this structure:
{
    "content": "full response", 
    "metadata": {
        "sources": ["source1", ...],
        "analysis": "technical notes",
        "next_steps": ["suggested actions"]
    }
}
- Escape special characters
- No markdown code blocks
- Include ALL data fields"""
                                messages[0]["content"] = system_msg

                            response = client.chat.completions.create(**api_params)
                        response_content = response.choices[0].message.content
                        
                        # Add response logging before storing
                        print(f"[Raw Reasoning Response]\n{response_content}\n{'='*50}")
                        # Store response with type information
                        output_data = {
                            'raw_response': response_content,
                            'response_type': 'actions' if '<actions>' in response_content else 'content'
                        }
                        
                        if job.response_format == 'json':
                            try:
                                output_data['response_json'] = json.loads(response_content)
                            except json.JSONDecodeError:
                                output_data['json_error'] = "Invalid JSON response"
                                
                        self.outputs[job.id] = output_data
                        self.output_buffer.append(response_content)
                    job.status = 'completed'
                except Exception as e:
                    job.status = f'failed: {str(e)}'
                    # Remove from queue whether successful or failed
                    self.job_queue.remove(job)
                    print(f"\n⚠️ Job {job.id} failed: {e}")

class AgentCLI(Cmd):
    prompt = 'agent> '
    
    def __init__(self):
        super().__init__()
        self.agent = Agent()
        self.initial_query = None
    
    def onecmd(self, line):
        """Override to handle natural language inputs properly"""
        if not line:
            return False
        if line.split()[0].lower() not in ['exit']:
            return self.default(line)
        return super().onecmd(line)
    
    def default(self, line):
        """Handle natural language queries"""
        if line.strip().lower() == 'exit':
            return self.do_exit('')
            
        # Reset agent state for new query
        self.agent = Agent()  # Fresh agent instance
        self.initial_query = line  # Store original query for potential reroll
        
        # Create fresh reasoning job
        self.agent.add_job(f"""<actions>
            <action id="0" type="reasoning">
                <content>Generate an XML action plan to: {line}</content>
            </action>
        </actions>""")
        self.agent.process_queue()
        self._handle_response()
    def _handle_response(self):
        """Process and display results"""
        # Add API key check first
        if not DEEPSEEK_API_KEY:
            print("\n❌ Missing DEEPSEEK_API_KEY environment variable")
            print("Get an API key from https://platform.deepseek.com")
            print("Then run: export DEEPSEEK_API_KEY=your_key_here")
            return
            
        # Find the initial reasoning job
        initial_job = next((job for job in self.agent.job_queue if job.id == "0"), None)
        
        if initial_job and initial_job.status.startswith('failed'):
            print(f"\n❌ Initial processing failed: {initial_job.status.split(':', 1)[-1].strip()}")
            return
                
        last_output = self.agent.outputs.get("0")
        
        if not last_output:
            print("\n🔍 No response received - Possible API issues or empty response")
            print("Check your DEEPSEEK_API_KEY environment variable")
            return
                
        response_content = last_output['raw_response']
        response_type = last_output.get('response_type')

        # Handle actions type first
        if response_type == 'actions' or '<actions>' in response_content:
            try:
                # Pre-process to remove markdown code fences if present
                if response_content.startswith('```xml'):
                    response_content = response_content[6:].strip()
                if response_content.endswith('```'):
                    response_content = response_content[:-3].strip()

                # Validate XML structure
                try:
                    root = ET.fromstring(response_content)
                except ET.ParseError as e:
                    print(f"\n❌ Invalid XML structure: {str(e)}")
                    print("Common fixes:")
                    print("1. Remove markdown code fences (```xml)")
                    print("2. Ensure proper tag nesting")
                    print("3. Escape special characters like & with &amp;")
                    return

                # Handle input requests first
                inputs = {}
                for req in root.findall('request_input'):
                    input_id = req.get('id')
                    prompt_text = req.get('desc') or req.text or "Please provide input:"
                    if not prompt_text.endswith(" "):  # Ensure space after prompt
                        prompt_text += " "
                    inputs[input_id] = input(prompt_text)
                
                # Add collected inputs to outputs
                self.agent.outputs.update(inputs)
                
                # Now handle actions
                action_xml = ET.tostring(root, encoding='unicode')
                self.agent.add_job(action_xml)
                
                # Get user confirmation
                print("\n" + "="*50 + "\n📋 Generated Plan\n" + "="*50)
                for job in self.agent.job_queue:
                    if job.status == 'pending':
                        icon = "🖥️" if job.type == 'bash' else "🐍" if job.type == 'python' else "💭"
                        deps = f"Deps: {', '.join(job.depends_on) or 'none'}"
                        model = f"Model: {job.model}" if job.model else ""
                        content_preview = job.content.split('\n')[0][:80] + ("..." if len(job.content) > 80 else "")
                        print(f"{icon} [{job.id}] {job.type.upper()} {model} | {deps} | {content_preview}")

                user_input = input("\n🚀 Execute these actions? (y/n/R) ").lower()
                if user_input == 'y':
                    print("\n[Executing Generated Plan]")
                    self.agent.process_queue()
                    self._show_results()
                elif user_input == 'r':
                    print("\n🔄 Regenerating plan...")
                    # Preserve initial query and reprocess
                    original_query = self.initial_query
                    self.agent = Agent()
                    self.agent.add_job(f"""<actions>
                        <action id="0" type="reasoning">
                            <content>Generate an XML action plan to: {original_query}</content>
                        </action>
                    </actions>""")
                    self.agent.process_queue()
                    self._handle_response()  # Recursively handle new plan
                else:
                    print("Execution canceled")
                    self.agent.job_queue = []
                    
            except ET.ParseError:
                print("\n[Final Answer]")
                print(response_content)
            return
        
        # Only handle as content if not actions
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
        print("\n" + "="*50 + "\n📊 Execution Results\n" + "="*50)
        for job in self.agent.job_queue:
            if job.id == "0":
                continue
                
            result = self.agent.outputs.get(job.id, {})
            status_icon = "✅" if job.status == 'completed' else "❌"
            icon = "🖥️" if job.type == 'bash' else "🐍" if job.type == 'python' else "💭"
            model = f"Model: {job.model}" if job.model else ""
            
            # Get output preview
            output = result.get('output') or result.get('raw_response') or str(result)
            output_preview = (output[:80] + "...") if len(output) > 80 else output
            
            print(f"{status_icon} {icon} [{job.id}] {job.type.upper()} {model}")
            print(f"   └─ {output_preview}")
            
        # Clear state after processing
        self.agent.job_queue = []
        self.agent.outputs = {}
        self.initial_query = None
    
    def do_exit(self, arg):
        """Exit the CLI"""
        return True

if __name__ == '__main__':
    AgentCLI().cmdloop()
