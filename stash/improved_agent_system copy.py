import time
import os
import shutil
from pathlib import Path
from dataclasses import dataclass
import xml.etree.ElementTree as ET
import subprocess
import os
import re
import warnings
import json
from cmd import Cmd
from openai import OpenAI
from prompts import INITIAL_SYSTEM_PROMPT, PLANNING_EXAMPLES, JSON_SYSTEM_PROMPT, CONTENT_SYSTEM_PROMPT
import traceback
import ast

# API Keys - SHOULD BE SET AS ENVIRONMENT VARIABLES
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

OPENROUTER_API_URL = 'https://openrouter.ai/api/v1'
GROQ_API_URL = 'https://api.groq.com/openai/v1'
DEEPSEEK_API_URL = 'https://api.deepseek.com'

@dataclass
class Job:
    """
    Represents a single job to be executed by the agent.

    Attributes:
        id (str): Unique identifier for the job.
        type (str): Type of job ('bash', 'python', or 'reasoning').
        content (str): The content of the job (e.g., bash script, python code, reasoning query).
        depends_on (list[str], optional): List of job IDs that this job depends on. Defaults to None.
        status (str, optional): Status of the job ('pending', 'completed', 'failed'). Defaults to 'pending'.
        output_ref (str, optional): Variable name to store output in. Defaults to None.
        model (str, optional): Model to use for reasoning jobs. Defaults to None.
        response_format (str, optional): Expected response format for reasoning jobs (e.g., 'json'). Defaults to None.
    """
    id: str
    type: str
    content: str
    depends_on: list[str] = None
    status: str = 'pending'
    output_ref: str = None
    model: str = None
    response_format: str = None

class Agent:
    """
    An agent that manages and executes jobs.
    """
    def __init__(self):
        """
        Initializes the Agent with an empty job queue, outputs dictionary, and output buffer.
        """
        self.job_queue = []
        self.outputs = {}  # Stores results of completed jobs
        self.output_buffer = []  # Global output accumulator

    def add_job(self, action_xml: str):
        """
        Adds jobs to the job queue based on the provided XML.

        Args:
            action_xml (str): An XML string containing job definitions.
        """
        try:
            root = ET.fromstring(action_xml)
            for action in root.findall('action'):
                job_id = action.get('id')
                job_type = action.get('type')
                content_element = action.find('content')
                if content_element is None:
                    content_element = action

                if content_element is None or content_element.text is None:
                    raise ValueError(f"Action {job_id} missing content element")

                content = content_element.text.strip()
                depends_on = action.get('depends_on', '').split(',') if action.get('depends_on') else []

                # More precise regex for dependency extraction
                template_deps = re.findall(r'\{\{outputs\.([\w\.]+)[^\}]*\}\}', content)
                depends_on += template_deps

                depends_on = list(set([d for d in depends_on if d]))
                depends_on = [d for d in depends_on if d]

                if job_type == 'reasoning':
                    model = action.get('model') or 'google/gemini-2.0-flash-001'
                    response_format = action.get('format')
                else:
                    model = None
                    response_format = None
                    if action.get('model') or action.get('format'):
                        warnings.warn(f"Ignoring model/format on {job_type} job {job_id}")

                self.job_queue.append(Job(
                    id=job_id,
                    type=job_type,
                    content=content,
                    depends_on=depends_on,
                    model=model,
                    response_format=response_format
                ))
        except ET.ParseError as e:
            print(f"Error parsing XML: {e}")
            raise
        except Exception as e:
            print(f"Error adding job: {e}")
            raise
    
    def _execute_reasoning_subjob(self, query, parent_id):
        """Handles nested model calls from Python scripts."""
        subjob_id = f"{parent_id}_sub"
        self.add_job(f"""<actions>
            <action id="{subjob_id}" type="reasoning">
                <content>{query}</content>
            </action>
        </actions>""")
        self.process_queue()
        return self.outputs.get(subjob_id, {}).get('raw_response', 'No response')
    
    def process_queue(self):
        """Processes the job queue, executing jobs based on their dependencies and type."""
        for job in list(self.job_queue):
            if job.status != 'pending':
                continue
            if all(self.outputs.get(dep) not in (None, 'No response') for dep in job.depends_on):
                try:
                    if job.type == 'bash':
                        self._execute_bash(job)
                    elif job.type == 'python':
                        self._execute_python(job)
                    elif job.type == 'reasoning':
                        self._execute_reasoning(job)
                    job.status = 'completed'
                except Exception as e:
                    self._handle_job_error(job, e)

    def _execute_bash(self, job):
        """Executes a bash job."""
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

        if result.returncode != 0:
            clean_output = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', result.stderr)
        else:
            clean_output = re.sub(r'\x1B\[[0-?]*[ -/]*[@-~]', '', result.stdout)

        clean_output = f"[Exit {result.returncode}] {clean_output.strip()}"
        self.outputs[job.id] = {
            'raw_response': clean_output,
            'output': clean_output,
            'status': 'completed'
        }
        self.output_buffer.append(clean_output)

    def _execute_python(self, job):
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
            # Use ast.literal_eval or a restricted environment for safer execution
            # For example:
            # compiled_code = compile(job.content, '<string>', 'exec')
            # exec(compiled_code, {'__builtins__': {}}, locs)
            exec(job.content, globals(), locs)  # Consider safer alternatives to exec
            output = buffer.getvalue()
            self.output_buffer.append(output)
            self.outputs[job.id] = {
                'raw_response': output,
                'output': output,
                'variables': locs
            }
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
            self.outputs[job.id] = {
                'error': {'error_msg': output},
                'output': output,
                'status': 'failed'
            }
        finally:
            sys.stdout = old_stdout

    def _execute_reasoning(self, job):
        """Executes a reasoning job."""
        print(f"[🤖] Running reasoning job '{job.id}' with model {job.model}")

        api_key = OPENROUTER_API_KEY
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable not set")

        client = OpenAI(
            api_key=api_key,
            base_url=OPENROUTER_API_URL,
        )
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            substitutions = {
                dep_id: str(self.outputs.get(dep_id, {}).get('raw_response', f'MISSING_OUTPUT_FOR_{dep_id}'))
                for dep_id in job.depends_on
            }
            processed_content = re.sub(
                r'\{\{outputs\.([\w\.]+)[^\}]*\}\}',
                lambda m: substitutions.get(m.group(1), 'MISSING_OUTPUT'),
                job.content
            )
            if job.id == "0" or job.id == 'initial-thought':
                system_msg = INITIAL_SYSTEM_PROMPT
                messages = [
                    {"role": "system", "content": system_msg},
                    *PLANNING_EXAMPLES,
                    {"role": "user", "content": processed_content}
                ]
            else:
                system_msg = CONTENT_SYSTEM_PROMPT
                messages = [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": processed_content}
                ]

            model = job.model or 'google/gemini-2.0-flash-001'
            api_params = {
                "model": model,
                "messages": messages,
                "stream": False
            }

            if job.response_format == 'json':
                api_params["response_format"] = {"type": "json_object"}
                system_msg = JSON_SYSTEM_PROMPT
                messages[0]["content"] = system_msg

            response = client.chat.completions.create(**api_params)
            response_content = response.choices[0].message.content
            if not response_content:
                raise ValueError("Empty response from API - check model permissions/availability")
            if response.choices[0].finish_reason != 'stop':
                print(f"\n⚠️ API WARNING: Finish reason '{response.choices[0].finish_reason}'")
                print(f"   Token Usage: {response.usage}")

            output_data = {
                'raw_response': response_content,
                'response_type': 'actions' if '<actions>' in response_content else 'content'
            }

            if job.response_format == 'json':
                output_data['response_json'] = {}
                try:
                    parsed_json = json.loads(response_content)
                    output_data['response_json'] = parsed_json
                except json.JSONDecodeError as e:
                    output_data['json_error'] = f"Invalid JSON: {str(e)}"
                    
            self.outputs[job.id] = output_data
            self.output_buffer.append(response_content)

    def _handle_job_error(self, job, e):
        """Handles job errors and logs details."""
        error_details = {
            'job_id': job.id,
            'job_type': job.type,
            'error_type': type(e).__name__,
            'error_msg': str(e),
            'dependencies': job.depends_on,
            'missing_deps': [d for d in job.depends_on if d not in self.outputs],
            'content_snippet': job.content[:200] + ('...' if len(job.content) > 200 else '')
        }

        if job.type == 'bash':
            error_details['command'] = job.content.split('\n')[0][:100]
        elif job.type == 'python':
            error_details['traceback'] = traceback.format_exc()
        elif job.type == 'reasoning':
            error_details['model'] = job.model
            error_details['response_format'] = job.response_format

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

        self.outputs[job.id] = {
            'error': error_details,
            'status': 'failed',
            'timestamp': time.time()
        }
        job.status = f'failed: {type(e).__name__}'

class AgentCLI(Cmd):
    """
    A command-line interface for interacting with the Agent.
    """
    prompt = 'agent> '

    def __init__(self):
        """
        Initializes the AgentCLI with an Agent instance, initial query, feedback history, and last generated plan XML.
        """
        super().__init__()
        self.agent = Agent()
        self.initial_query = None
        self.feedback_history = []  # Track feedback across regenerations
        self.last_generated_plan_xml = None  # Store original XML for recall

    def onecmd(self, line):
        """
        Override to handle natural language inputs properly.
        Handles commands that start with 'job:' and delegates other inputs to the default method.
        """
        if not line:
            return False
        if line.lower().startswith("job:") or line.split()[0].lower() not in ['exit']:
            return self.default(line)
        return super().onecmd(line)

    def default(self, line):
        """
        Handles natural language queries and executes job plans.
        Resets the agent state for new queries and initiates the planning process.
        """
        if line.strip().lower() == 'exit':
            return self.do_exit('')
            
        if line.lower().startswith("job:"):
            job_name = line.split(":", 1)[1].strip()
            try:
                xml_content = self._load_job(job_name)
                print(f"📂 Executing saved job: {job_name}")
                self.agent = Agent()
                self.agent.add_job(xml_content)
                self.last_generated_plan_xml = xml_content
                self.agent.process_queue()
                self._show_results()
            except Exception as e:
                print(f"❌ Error loading job: {str(e)}")
            return

        self.agent = Agent()  # Fresh agent instance
        self.initial_query = line  # Store original query for potential reroll

        self.agent.add_job(f"""<actions>
            <action id="0" type="reasoning">
                <content>
                    Generate an XML action plan to: {line}

                    Requirements:
                    - Ensure any Python code is properly formatted with dependencies installed
                    - Pass data between jobs appropriately
                    - If a Python job depends on reasoning output, that reasoning job must use format="json"
                    - Break long-form content into manageable chunks
                </content>
            </action>
        </actions>""")
        self.agent.process_queue()
        self._handle_response()

    def _handle_response(self):
        """Processes the agent's response and displays the results or handles user feedback."""
        if not DEEPSEEK_API_KEY:
            print("\n❌ Missing DEEPSEEK_API_KEY environment variable")
            print("Get an API key from https://platform.deepseek.com")
            print("Then run: export DEEPSEEK_API_KEY=your_key_here")
            return

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

        if response_type == 'actions':
            try:
                if response_content.startswith('```xml'):
                    response_content = response_content[6:].strip()
                if response_content.endswith('```'):
                    response_content = response_content[:-3].strip()

                try:
                    root = ET.fromstring(response_content)
                except ET.ParseError as e:
                    print(f"\n❌ Invalid XML structure: {str(e)}")
                    print("Common fixes:")
                    print("1. Remove markdown code fences (```xml)")
                    print("2. Ensure proper tag nesting")
                    print("3. Escape special characters like & with &amp;")
                    return

                self.last_generated_plan_xml = response_content

                inputs = {}
                for req in root.findall('request_input'):
                    input_id = req.get('id')
                    prompt_text = req.get('desc') or req.text or "Please provide input:"
                    if not prompt_text.endswith(" "):
                        prompt_text += " "
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
                    self.agent.add_job(f"""<actions>
                        <action id="0" type="reasoning">
                            <content>Generate an XML action plan to: {original_query}{feedback_clause}</content>
                        </action>
                    </actions>""")
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
        """Displays the execution results of the jobs in the agent's queue."""
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
            print("  s - Save current job plan")
            print("  exit - Return to prompt")
            choice = input("agent(post)> ").lower()
            
            if choice == 'q':
                self.agent = Agent()
                self.agent.add_job(f"""<actions>
                    <action id="0" type="reasoning">
                        <content>Generate an XML action plan to: {self.initial_query}</content>
                    </action>
                </actions>""")
                self.agent.process_queue()
                self._handle_response()
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
                    self.agent.add_job(f"""<actions>
                        <action id="0" type="reasoning">
                            <content>Generate an XML action plan to: {self.initial_query}{feedback_clause}
                            
                            Previous Plan:
                            {self.last_generated_plan_xml}

                            Don't change anything unless the user has specifically requested it.
                            </content>
                        </action>
                    </actions>""")
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
            elif choice == 's':
                job_name = input("Enter name to save job as: ").strip()
                if job_name:
                    self._save_job(job_name)
            elif choice == 'exit':
                break
            else:
                print("Invalid option")

        self.agent.job_queue = []
        self.agent.outputs = {}
        self.initial_query = None

    def _save_job(self, job_name: str):
        """Saves the last generated XML plan to a file in the 'jobs' directory."""
        jobs_dir = Path("jobs")
        jobs_dir.mkdir(exist_ok=True)

        if not self.last_generated_plan_xml:
            print("No plan to save")
            return

        dest = jobs_dir / f"{job_name}.xml"
        dest.write_text(self.last_generated_plan_xml, encoding='utf-8')
        print(f"✅ Saved job to {dest}")

    def _load_job(self, job_name: str) -> str:
        """Loads an XML plan from a file in the 'jobs' directory."""
        job_path = Path("jobs") / f"{job_name}.xml"
        if not job_path.exists():
            raise FileNotFoundError(f"Job {job_name} not found")
        return job_path.read_text(encoding='utf-8')

    def do_exit(self, arg):
        """Exits the command-line interface."""
        return True

if __name__ == '__main__':
    AgentCLI().cmdloop()