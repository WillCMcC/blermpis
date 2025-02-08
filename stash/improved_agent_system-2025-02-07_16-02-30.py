import time
import os
import shutil
from pathlib import Path
from dataclasses import dataclass, field
import xml.etree.ElementTree as ET
import subprocess
import re
import warnings
import json
from cmd import Cmd
from openai import OpenAI
from prompts import INITIAL_SYSTEM_PROMPT, PLANNING_EXAMPLES, JSON_SYSTEM_PROMPT, CONTENT_SYSTEM_PROMPT
import logging
import uuid
from typing import List, Dict, Any, Optional

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# API Keys (Use environment variables)
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

# API URLs
OPENROUTER_API_URL = 'https://openrouter.ai/api/v1'
GROQ_API_URL = 'https://api.groq.com/openai/v1'
DEEPSEEK_API_URL = 'https://api.deepseek.com'

@dataclass
class Job:
    id: str
    type: str  # 'bash', 'python', or 'reasoning'
    content: str
    depends_on: list[str] = field(default_factory=list)
    status: str = 'pending'
    output_ref: str = None  # Variable name to store output in
    model: str = None  # Add model parameter
    response_format: str = None  # Add response format field

class Agent:
    def __init__(self):
        self.job_queue: List[Job] = []
        self.outputs: Dict[str, Any] = {}  # Stores results of completed jobs
        self.output_buffer: List[str] = []  # Global output accumulator
        
    def add_job(self, action_xml: str):
        """Adds a job to the job queue based on the provided XML."""
        try:
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
                depends_on = [d for d in action.get('depends_on', '').split(',') if d]
                
                # Add implicit dependencies from template variables
                template_deps = re.findall(r'\{\{outputs\.([\w]+)[^\}]*\}\}', content)  # Capture only job ID before first dot
                depends_on += template_deps
                
                # Remove duplicates and empty strings
                depends_on = list(set([d for d in depends_on if d]))
                
                # Only allow model/format for reasoning jobs
                model = None
                response_format = None
                if job_type == 'reasoning':
                    model = action.get('model') or 'google/gemini-2.0-flash-001'
                    response_format = action.get('format')
                else:  # Python/bash jobs can't have model/format
                    if action.get('model') or action.get('format'):
                        raise ValueError(f"Model/format not allowed on {job_type} job {job_id}")

                self.job_queue.append(Job(
                    id=job_id,
                    type=job_type,
                    content=content,
                    depends_on=depends_on,
                    model=model,  # Add model parameter
                    response_format=response_format  # Store response format
                ))
        except ET.ParseError as e:
            logging.error(f"XML Parse Error: {e}")
            raise

    def _execute_reasoning_subjob(self, query: str, parent_id: str) -> str:
        """Handle nested model calls from Python scripts"""
        subjob_id = f"{parent_id}_sub_{uuid.uuid4()}"  # Use UUID
        self.add_job(f"""<actions>
            <action id="{subjob_id}" type="reasoning">
                <content>{query}</content>
            </action>
        </actions>""")
        self.process_queue()
        return self.outputs.get(subjob_id, {}).get('raw_response', 'No response')

    def _execute_bash_job(self, job: Job):
        """Executes a bash job."""
        try:
            # Inject dependency outputs as environment variables
            env_vars = os.environ.copy()
            # Sanitize environment variables (example - add more as needed)
            env_vars.update({
                f'OUTPUT_{dep_id}': str(self.outputs.get(dep_id)) for dep_id in job.depends_on
            })
            env_vars['PS1'] = ''  # Cleaner output

            result = subprocess.run(
                job.content,
                shell=False,  # Avoid shell=True
                capture_output=True,
                text=True,
                env=env_vars,
                executable='/bin/bash'  # Explicitly use bash
            )

            # Clean ANSI escape codes and handle errors
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
            logging.info(f"Bash job '{job.id}' completed successfully.")
        except Exception as e:
            logging.error(f"Bash job '{job.id}' failed: {e}", exc_info=True)  # Log with traceback
            self.outputs[job.id] = {
                'error': {'error_msg': str(e)},
                'output': f"Bash Error: {str(e)}",
                'status': 'failed'
            }

    def _execute_python_job(self, job: Job):
        """Executes a Python job."""
        try:
            import sys
            from io import StringIO
            old_stdout = sys.stdout
            sys.stdout = buffer = StringIO()

            try:
                # Create a minimal and safe execution environment
                locs = {
                    'outputs': {dep: self.outputs.get(dep) for dep in job.depends_on},
                    'agent': self,  # Add agent reference (carefully)
                    'model_call': lambda query: self._execute_reasoning_subjob(query, job.id),
                    'get_output': lambda key: self.outputs.get(key),
                    'json': json,  # Make json available to scripts
                    'append_output': lambda text: self.output_buffer.append(str(text)),
                    # Removed dangerous functions
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
                logging.info(f"Python job '{job.id}' completed successfully.")
            except Exception as e:
                error_msg = str(e)
                output = f"Python Error: {error_msg}"
                self.outputs[job.id] = {
                    'error': {'error_msg': output},
                    'output': output,
                    'status': 'failed'
                }
                logging.error(f"Python job '{job.id}' failed: {e}", exc_info=True)
            finally:
                sys.stdout = old_stdout

    def _execute_reasoning_job(self, job: Job):
        """Executes a reasoning job."""
        try:
            # Add query logging
            logging.info(f"Running reasoning job '{job.id}' with model {job.model}")

            api_key = OPENROUTER_API_KEY
            if not api_key:
                raise ValueError("OPENROUTER_API_KEY environment variable not set") # Correct API Key used

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
                if job.id == "0" or job.id == 'initial-thought':  # Initial planning job
                    system_msg = INITIAL_SYSTEM_PROMPT
                    # Create messages array with examples
                    messages = [
                        {"role": "system", "content": system_msg},
                        *PLANNING_EXAMPLES,
                        {"role": "user", "content": processed_content}
                    ]
                else:  # Subsequent reasoning queries
                    system_msg = CONTENT_SYSTEM_PROMPT
                    messages = [
                        {"role": "system", "content": system_msg},
                        {"role": "user", "content": processed_content}
                    ]

                # Use job-specific model if specified, else deepseek-r1
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
                    system_msg = JSON_SYSTEM_PROMPT
                    messages[0]["content"] = system_msg

            response = client.chat.completions.create(**api_params)
            response_content = response.choices[0].message.content
            finish_reason = response.choices[0].finish_reason
            if not response_content:
                raise ValueError("Empty response from API - check model permissions/availability")
            if finish_reason != 'stop':
                logging.warning(f"API WARNING: Finish reason '{finish_reason}'")
                logging.warning(f"Token Usage: {response.usage}")

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

            self.outputs[job.id] = output_data
            self.output_buffer.append(response_content)
            logging.info(f"Reasoning job '{job.id}' completed successfully.")
        except Exception as e:
            logging.error(f"Reasoning job '{job.id}' failed: {e}", exc_info=True)
            self.outputs[job.id] = {
                'error': {'error_msg': str(e)},
                'status': 'failed'
            }

    def process_queue(self):
        """Processes jobs in the queue."""
        while True:
            pending_jobs = [job for job in self.job_queue if job.status == 'pending']
            if not pending_jobs:
                break

            for job in pending_jobs:
                if all(self.outputs.get(dep) not in (None, 'No response') for dep in job.depends_on):
                    try:
                        if job.type == 'bash':
                            self._execute_bash_job(job)
                        elif job.type == 'python':
                            self._execute_python_job(job)
                        elif job.type == 'reasoning':
                            self._execute_reasoning_job(job)
                        else:
                            raise ValueError(f"Unknown job type: {job.type}")
                        job.status = 'completed'
                    except Exception as e:
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
                            import traceback
                            error_details['traceback'] = traceback.format_exc()
                        elif job.type == 'reasoning':
                            error_details['model'] = job.model
                            error_details['response_format'] = job.response_format
                        
                        # Build rich error message
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
                        
                        # Store error details in output
                        self.outputs[job.id] = {
                            'error': error_details,
                            'status': 'failed',
                            'timestamp': time.time()
                        }
                        
                        # Keep failed jobs in queue for inspection
                        job.status = f'failed: {type(e).__name__}'


class AgentCLI(Cmd):
    prompt = 'agent> '
    
    def __init__(self):
        super().__init__()
        self.agent = Agent()
        self.initial_query = None
        self.feedback_history: List[str] = []  # Track feedback across regenerations
        self.last_generated_plan_xml = None  # Store original XML for recall
    
    def onecmd(self, line: str) -> bool:
        """Override to handle natural language inputs properly"""
        if not line:
            return False
        if line.lower().startswith("job:") or line.split()[0].lower() not in ['exit']:
            return self.default(line)
        return super().onecmd(line)
    
    def default(self, line: str) -> None:
        """Handle natural language queries and job execution"""
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

        # Reset agent state for new query
        self.agent = Agent()  # Fresh agent instance
        self.initial_query = line  # Store original query for potential reroll
        
        # Create initial planning job
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

    def _handle_response(self) -> None:
        """Process and display results iteratively."""
        while True:
            # Add API key check first
            if not OPENROUTER_API_KEY: #Correct API Key check
                print("\n❌ Missing OPENROUTER_API_KEY environment variable")
                print("Get an API key from https://openrouter.ai")
                print("Then run: export OPENROUTER_API_KEY=your_key_here")
                return

            # Find the initial reasoning job
            initial_job = next((job for job in self.agent.job_queue if job.id == "0"), None)

            if initial_job and initial_job.status.startswith('failed'):
                print(f"\n❌ Initial processing failed: {initial_job.status.split(':', 1)[-1].strip()}")
                return
                    
            last_output = self.agent.outputs.get("0")
            
            if not last_output:
                print("\n🔍 No response received - Possible API issues or empty response")
                print("Check your OPENROUTER_API_KEY environment variable") #Correct API Key check
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
                        break  # Exit loop after invalid XML

                    # Store the original XML plan
                    self.last_generated_plan_xml = response_content

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
                        user_input = 'r'  # Treat feedback as regeneration request
                    
                    if user_input == 'y':
                        print("\n[Executing Generated Plan]")
                        self.agent.process_queue()
                        self._show_results()
                        break  # Exit loop after execution
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
                        # self._handle_response()  # Recursively handle new plan
                        continue  # Continue loop for regeneration
                    else:
                        print("Execution canceled")
                        self.agent.job_queue = []
                        break # Exist loop when execution cancelled
                
                except ET.ParseError:
                    print("\n[Final Answer]")
                    print(response_content)
                return #added return
            
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
            break #added break

    def _show_results(self) -> None:
        """Displays the execution results."""
        print("\n" + "="*50 + "\n📊 Execution Results\n" + "="*50)
        for job in self.agent.job_queue:
            if job.id == "0":  # Skip planning job
                continue
                
            result = self.agent.outputs.get(job.id, {})
            status_icon = "✅" if job.status == 'completed' else "❌"
            icon = "🖥️" if job.type == 'bash' else "🐍" if job.type == 'python' else "💭"
            model = f"Model: {job.model}{' [JSON]' if job.response_format == 'json' else ''}" if job.model else ""
            
            # Get output preview
            output = result.get('error', {}).get('error_msg') or result.get('output') or result.get('raw_response') or str(result)
            output_preview = (output[:80] + "...") if len(output) > 80 else output
            
            print(f"{status_icon} {icon} [{job.id}] {job.type.upper()} {model}")
            print(f"   └─ {output_preview}")
            
        # Add post-execution options
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
                    job_saved = self._save_job(job_name)
                    if not job_saved:  # If save fails, don't exit
                        continue
            elif choice == 'exit':
                break
            else:
                print("Invalid option")

        # Clear state after processing
        self.agent.job_queue = []
        self.agent.outputs = {}
        self.initial_query = None
    
    def _save_job(self, job_name: str) -> bool:
        """Save the current XML plan to jobs directory"""
        jobs_dir = Path("jobs")
        jobs_dir.mkdir(exist_ok=True)
        
        if not self.last_generated_plan_xml:
            print("No plan to save")
            return False
            
        dest = jobs_dir / f"{job_name}.xml"
        try:
            dest.write_text(self.last_generated_plan_xml, encoding='utf-8')
            print(f"✅ Saved job to {dest}")
            return True
        except IOError as e:
            print(f"❌ Error saving job: {e}")
            return False

    def _load_job(self, job_name: str) -> str:
        """Load XML from jobs directory"""
        job_path = Path("jobs") / f"{job_name}.xml"
        try:
            return job_path.read_text(encoding='utf-8')
        except FileNotFoundError:
            raise FileNotFoundError(f"Job {job_name} not found")
        except IOError as e:
            raise IOError(f"Error reading job {job_name}: {e}")

    def do_exit(self, arg: str) -> bool:
        """Exit the CLI"""
        return True

if __name__ == '__main__':
    AgentCLI().cmdloop()