import time
import os
import shutil
from dataclasses import dataclass
import xml.etree.ElementTree as ET
import subprocess
import re
import warnings
import json
import requests
from cmd import Cmd
from openai import OpenAI
import logging  # Corrected import
from prompts import INITIAL_SYSTEM_PROMPT, PLANNING_EXAMPLES, JSON_SYSTEM_PROMPT, CONTENT_SYSTEM_PROMPT
import shlex
import contextlib
from queue import Queue
import threading

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')

# Constants
DEEPSEEK_API_KEY = os.environ.get('DEEPSEEK_API_KEY')
OPENROUTER_API_KEY = os.environ.get('OPENROUTER_API_KEY')
GROQ_API_KEY = os.environ.get('GROQ_API_KEY')

OPENROUTER_API_URL = 'https://openrouter.ai/api/v1'
GROQ_API_URL = 'https://api.groq.com/openai/v1'
DEEPSEEK_API_URL = 'https://api.deepseek.com'

OUTPUT_PREVIEW_LENGTH = 80
JOB_CONTENT_SNIPPET_LENGTH = 200

# Precompile regex pattern
TEMPLATE_VAR_PATTERN = re.compile(r'\{\{outputs\.([\w]+)[^\}]*\}\}')

@dataclass
class Job:
    id: str
    type: str  # 'bash', 'python', or 'reasoning'
    content: str
    depends_on: list[str] = None  # Changed to None as default
    status: str = 'pending'
    output_ref: str = None  # Variable name to store output in
    model: str = None  # Add model parameter
    response_format: str = None  # Add response format field

    def __post_init__(self):
        if self.depends_on is None:
            self.depends_on = []

class Agent:
    MAX_RECURSION_DEPTH = 5

    def __init__(self):
        self.job_queue = []
        self.outputs = {}  # Stores results of completed jobs
        self.output_buffer = []  # Global output accumulator
        self.recursion_depth = 0  # Track recursion depth

    def add_job(self, action_xml: str):
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

                content = content_element.text.strip() if content_element.text else ""
                depends_on = action.get('depends_on', '').split(',') if action.get('depends_on') else []

                # Add implicit dependencies from template variables
                template_deps = TEMPLATE_VAR_PATTERN.findall(content)
                depends_on += template_deps

                # Remove duplicates and empty strings
                depends_on = list(set([d for d in depends_on if d]))

                # Only allow model/format for reasoning jobs
                if job_type == 'reasoning':
                    model = action.get('model') or 'google/gemini-2.0-flash-001'
                    response_format = action.get('format')
                else:  # Python/bash jobs can't have model/format
                    model = None
                    response_format = None
                    if action.get('model') or action.get('format'):
                        logging.warning(f"Ignoring model/format on {job_type} job {job_id}")
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

    def _execute_reasoning_subjob(self, query, parent_id):
        """Handle nested model calls from Python scripts"""
        if self.recursion_depth >= self.MAX_RECURSION_DEPTH:
            logging.error("Maximum recursion depth exceeded in reasoning subjob.")
            return "Error: Maximum recursion depth exceeded."
        
        self.recursion_depth += 1
        try:
            subjob_id = f"{parent_id}_sub"
            self.add_job(f"""<actions>
                <action id="{subjob_id}" type="reasoning">
                    <content>{query}</content>
                </action>
            </actions>""")
            self.process_queue()
            return self.outputs.get(subjob_id, {}).get('raw_response', 'No response')
        finally:
            self.recursion_depth -= 1

    def process_queue(self):
        job_queue = Queue()  # Use queue.Queue for thread-safe operations
        for job in self.job_queue:  # Iterate copy of queue
            job_queue.put(job)
            
        def worker():
            while True:
                try:
                    job = job_queue.get(timeout=5)  # Added timeout
                except queue.Empty:
                    break  # Exit if queue is empty
                if job is None or job.status != 'pending':
                    job_queue.task_done() # Mark work as done even if skipped
                    continue

                try:
                    if job.type == 'bash':
                        self._execute_bash_job(job)
                    elif job.type == 'python':
                        self._execute_python_job(job)
                    elif job.type == 'input':
                        self._execute_input_job(job)
                    elif job.type == 'reasoning':
                        self._execute_reasoning_job(job)
                    job.status = 'completed'
                except Exception as e:
                    self._handle_job_failure(job, e)
                finally:
                    job_queue.task_done()

        num_threads = min(4, len(self.job_queue))  # Limit the number of threads
        threads = []
        for _ in range(num_threads):
            t = threading.Thread(target=worker)
            threads.append(t)
            t.start()

        # Block until all tasks are done
        job_queue.join()

        for t in threads:
            t.join()
            
    def _execute_bash_job(self, job):
        """Executes a bash job."""
        # Inject dependency outputs as environment variables
        env_vars = os.environ.copy()
        env_vars.update({f'OUTPUT_{dep_id}': str(self.outputs.get(dep_id))
                        for dep_id in job.depends_on})
        env_vars['PS1'] = ''  # Cleaner output

        try:
            # Use shlex.split to avoid shell=True
            command_list = shlex.split(job.content)
            result = subprocess.run(
                command_list,
                capture_output=True,
                text=True,
                env=env_vars,
                timeout=30  # Added timeout
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
        except subprocess.TimeoutExpired:
            clean_output = "Bash Error: Timeout expired"
            self.outputs[job.id] = {
                    'error': {'error_msg': clean_output},
                    'output': clean_output,
                    'status': 'failed'
                }
    def _execute_python_job(self, job):
        """Executes a python job."""
         # Capture printed output
        import sys
        from io import StringIO
        
        locs = {
            'outputs': {dep: self.outputs.get(dep) for dep in job.depends_on},
            'agent': self,  # Add agent reference
            'model_call': lambda query: self._execute_reasoning_subjob(query, job.id),
            'get_output': lambda key: self.outputs.get(key),
            'json': json,  # Make json available to scripts
            'requests': requests,  # Make request available to scripts
            'append_output': lambda text: self.output_buffer.append(str(text)),
            'validate_json': lambda data, keys: all(k in data for k in keys),
            'get_json_field': lambda job_id, field: self.outputs.get(job_id, {}).get('response_json', {}).get('content', {}).get(field)
        }
        
        try:
            with contextlib.redirect_stdout(StringIO()) as buffer:
                exec(job.content, globals(), locs)
                output = buffer.getvalue()
                self.output_buffer.append(output)
                # Store output with raw_response for consistent access
                self.outputs[job.id] = {
                    'raw_response': output,  # Add raw_response field
                    'output': output,
                    'variables': locs
                }
        except json.JSONDecodeError as e:
            error_msg = f"JSONDecodeError: {str(e)}"
            error_msg += "\n🔍 JSON ISSUE - Possible fixes:" \
                        "\n1. Verify previous job uses format=\"json\"" \
                        "\n2. Check outputs[\"ID\"][\"json_error\"] for parsing issues" \
                        "\n3. Access fields via outputs[\"ID\"][\"response_json\"][\"content\"]"
            self._handle_python_error(job, error_msg)
        except Exception as e:
            error_msg = str(e)
            self._handle_python_error(job, error_msg)

    def _handle_python_error(self, job, error_msg):
        """Handles Python job errors."""
        missing_deps = [d for d in job.depends_on if d not in self.outputs]
        if missing_deps:
            error_msg += f"\n🔗 MISSING DEPENDENCIES: {', '.join(missing_deps)}"
        output = f"Python Error: {error_msg}"
        self.outputs[job.id] = {
            'error': {'error_msg': output},
            'output': output,
            'status': 'failed'
        }

    def _execute_input_job(self, job):
        """Executes an input job."""
        # Get prompt from content or default
        prompt = job.content.strip() or "Please provide input: "
        if not prompt.endswith(" "):
            prompt += " "

        # Get user input and store it
        user_input = input(prompt)
        self.outputs[job.id] = {
            'raw_response': user_input,
            'output': user_input,
            'status': 'completed'
        }
        self.output_buffer.append(user_input)

    def _execute_reasoning_job(self, job):
        """Executes a reasoning job."""
        # Add query logging
        logging.info(f"[🤖] Running reasoning job '{job.id}' with model {job.model}")

        api_key = OPENROUTER_API_KEY
        if not api_key:
            raise ValueError("OPENROUTER_API_KEY environment variable not set")

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

            # Use job-specific model if specified, else google/gemini-2.0-flash-001
            model = job.model or 'google/gemini-2.0-flash-001'

            # Prepare API parameters
            api_params = {
                "model": model,
                "messages": messages,
                "stream": False,
                "timeout": 60
            }

            if job.response_format == 'json':
                api_params["response_format"] = {"type": "json_object"}
                messages[0]["content"] = JSON_SYSTEM_PROMPT  # Update system message for JSON responses

            try:
                response = client.chat.completions.create(**api_params)
                response_content = response.choices[0].message.content
                if not response_content:
                    raise ValueError("Empty response from API - check model permissions/availability")
                if response.choices[0].finish_reason != 'stop':
                    logging.warning(f"\n⚠️ API WARNING: Finish reason '{response.choices[0].finish_reason}'")
                    logging.warning(f"   Token Usage: {response.usage}")
                response_content = response.choices[0].message.content

                # Add response logging before storing
                # logging.info(f"[Raw Reasoning Response]\n{response_content}\n{'='*50}")
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
            except requests.exceptions.RequestException as e:
                # Handle network-related errors (e.g., timeout, connection error)
                logging.error(f"API Request Error: {e}")
                raise
            except Exception as e:
                logging.error(f"Unexpected Error with API Call: {e}")
                raise # Re-raise to be handled in parent
                
    def _handle_job_failure(self, job, e):
        """Handles job failures."""
        error_details = {
            'job_id': job.id,
            'job_type': job.type,
            'error_type': type(e).__name__,
            'error_msg': str(e),
            'dependencies': job.depends_on,
            'missing_deps': [d for d in job.depends_on if d not in self.outputs],
            'content_snippet': job.content[:JOB_CONTENT_SNIPPET_LENGTH] + ('...' if len(job.content) > JOB_CONTENT_SNIPPET_LENGTH else '')
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
        logging.error('\n'.join(error_lines))

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

    def __init__(self, user_input_fn=input):
        super().__init__()
        self.agent = Agent()
        self.initial_query = None
        self.feedback_history = []  # Track feedback across regenerations
        self.last_generated_plan_xml = None  # Store original XML for recall
        self.user_input_fn = user_input_fn  # Injectable user input function

        # Show welcome message
        logging.info("\n🤖 Welcome to AgentCLI!")
        logging.info("\nQuick Start:")
        logging.info("1. Type your request in natural language")
        logging.info("2. Type 'j' to list and run saved jobs")
        logging.info("3. Type 'exit' to quit")
        logging.info("\nExample: create a python script that prints hello world")
        logging.info("="*50)

    def onecmd(self, line):
        """Override to handle natural language inputs properly"""
        if not line:
            return False
        if line.lower().startswith("job:") or line.split()[0].lower() not in ['exit', 'j']:
            return self.default(line)
        return super().onecmd(line)

    def default(self, line):
        """Handle natural language queries and job execution"""
        if line.strip().lower() == 'exit':
            return self.do_exit('')

        if line.lower().startswith("job:"):
            job_name = line.split(":", 1)[1].strip()
            try:
                xml_content = self._load_job(job_name)
                logging.info(f"📂 Executing saved job: {job_name}")
                self.agent = Agent()
                self.agent.add_job(xml_content)
                self.last_generated_plan_xml = xml_content
                self.agent.process_queue()
                self._show_results()
            except Exception as e:
                logging.error(f"❌ Error loading job: {str(e)}")
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
            <action id="1" type="reasoning" depends_on="0" model="google/gemini-2.0-flash-001">
                <content>
                    Review and improve this plan for efficiency and correctness:
                    {{{{outputs.0.raw_response}}}}

                    Consider:
                    1. Are dependencies properly declared?
                    2. Are appropriate formats (JSON/text) used?
                    3. Can steps be parallelized?
                    4. Are error handling measures in place?

                    Return ONLY the improved XML plan.
                </content>
            </action>
        </actions>""")
        self.agent.process_queue()
        self._handle_response()

    def _handle_response(self):
        """Process and display results"""
        # Add API key check first
        if not OPENROUTER_API_KEY:
            logging.error("\n❌ Missing OPENROUTER_API_KEY environment variable")
            logging.error("Get an API key from https://openrouter.ai")
            logging.error("Then run: export OPENROUTER_API_KEY=your_key_here")
            return

        # Find the initial reasoning job
        initial_job = next((job for job in self.agent.job_queue if job.id == "1"), None)

        if initial_job and initial_job.status.startswith('failed'):
            logging.error(f"\n❌ Initial processing failed: {initial_job.status.split(':', 1)[-1].strip()}")
            return

        last_output = self.agent.outputs.get("1")

        if not last_output:
            logging.warning("\n🔍 No response received - Possible API issues or empty response")
            logging.warning("Check your OPENROUTER_API_KEY environment variable")
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
                    logging.error(f"\n❌ Invalid XML structure: {str(e)}")
                    logging.error("Common fixes:")
                    logging.error("1. Remove markdown code fences (```xml)")
                    logging.error("2. Ensure proper tag nesting")
                    logging.error("3. Escape special characters like & with &amp;")
                    return

                # Store the original XML plan
                self.last_generated_plan_xml = response_content

                # Handle input requests first
                inputs = {}
                for req in root.findall('request_input'):
                    input_id = req.get('id')
                    prompt_text = req.get('desc') or req.text or "Please provide input:"
                    if not prompt_text.endswith(" "):  # Ensure space after prompt
                        prompt_text += " "
                    inputs[input_id] = self.user_input_fn(prompt_text)

                # Add collected inputs to outputs
                self.agent.outputs.update(inputs)

                # Now handle actions
                action_xml = ET.tostring(root, encoding='unicode')
                self.agent.add_job(action_xml)

                # Get user confirmation
                logging.info("\n" + "="*50 + "\n📋 Generated Plan\n" + "="*50)
                for job in self.agent.job_queue:
                    if job.status == 'pending' and job.id != "0":
                        icon = "🖥️" if job.type == 'bash' else "🐍" if job.type == 'python' else "💭"
                        deps = f"Deps: {', '.join(job.depends_on) or 'none'}"
                        model = f"Model: {job.model}{' [JSON]' if job.response_format == 'json' else ''}" if job.type == 'reasoning' else ""

                        logging.info(f"{icon} [{job.id}] {job.type.upper()} {model}")
                        logging.info(f"   └─ Dependencies: {deps}")
                        logging.info("      Content:")
                        for line in job.content.split('\n'):
                            logging.info(f"      │ {line}")
                        logging.info("─"*50)

                user_input = self.user_input_fn("\n🚀 Options: (y) Execute, (n) Cancel, (R) Regenerate, (!) Add feedback: ").lower()
                feedback = None
                if user_input.startswith('!'):
                    feedback = user_input[1:].strip()
                    logging.info(f"📝 Feedback noted: {feedback}")
                    user_input = 'r'  # Treat feedback as regeneration request

                if user_input == 'y':
                    logging.info("\n[Executing Generated Plan]")
                    self.agent.process_queue()
                    self._show_results()
                elif user_input == 'r':
                    logging.info("\n🔄 Regenerating plan with feedback...")
                    original_query = self.initial_query
                    if feedback:
                        self.feedback_history.append(feedback)
                    
                    # Limit feedback history (e.g., keep only the last 3 messages)
                    self.feedback_history = self.feedback_history[-3:]

                    feedback_clause = ""
                    if self.feedback_history:
                        feedback_clause = "\n\nFeedback from previous plans:\n- " + "\n- ".join(self.feedback_history)

                    self.agent = Agent()
                    self.agent.add_job(f"""<actions>
                        <action id="0" type="reasoning">
                            <content>Generate an XML action plan to: {original_query}{feedback_clause}</content>
                        </action>
                        <action id="1" type="reasoning" depends_on="0" model="google/gemini-2.0-flash-001">
                            <content>
                                Review and improve this plan:
                                {{{{outputs.0.raw_response}}}}
                                Consider user feedback: {feedback}

                                Return ONLY the improved XML plan.
                            </content>
                        </action>
                    </actions>""")
                    self.agent.process_queue()
                    self._handle_response()  # Recursively handle new plan
                else:
                    logging.info("Execution canceled")
                    self.agent.job_queue = []

            except ET.ParseError:
                logging.info("\n[Final Answer]")
                logging.info(response_content)
            return

        # Only handle as content if not actions
        logging.info("\n[Assistant Response]")
        try:
            wrapped = f"<wrapper>{response_content}</wrapper>"
            root = ET.fromstring(wrapped)
            response_node = root.find('.//response')
            if response_node is not None and response_node.text:
                logging.info(response_node.text.strip())
            else:
                logging.info("Received empty response")
        except ET.ParseError:
            logging.info(response_content)

    def _show_results(self):
        logging.info("\n" + "="*50 + "\n📊 Execution Results\n" + "="*50)
        for job in self.agent.job_queue:
            if job.id == "0":  # Skip planning job
                continue

            result = self.agent.outputs.get(job.id, {})
            status_icon = "✅" if job.status == 'completed' else "❌"
            icon = "🖥️" if job.type == 'bash' else "🐍" if job.type == 'python' else "💭"
            model = f"Model: {job.model}{' [JSON]' if job.response_format == 'json' else ''}" if job.model else ""

            # Get output preview
            output = result.get('error', {}).get('error_msg') or result.get('output') or result.get('raw_response') or str(result)
            output_preview = (output[:OUTPUT_PREVIEW_LENGTH] + "...") if len(output) > OUTPUT_PREVIEW_LENGTH else output

            logging.info(f"{status_icon} {icon} [{job.id}] {job.type.upper()} {model}")
            logging.info(f"   └─ {output_preview}")

        # Refactor post-execution options using a state machine
        state = "post_execution"
        while state != "exit":
            logging.info("\nPost-execution options:")
            logging.info("  q - Rerun original query")
            logging.info("  p - Rerun last plan")
            logging.info("  f - Add feedback & regenerate")
            logging.info("  x - Show generated XML plan")
            logging.info("  s - Save current job plan")
            logging.info("  exit - Return to prompt")
            choice = self.user_input_fn("agent(post)> ").lower()

            if choice == 'q':
                self._rerun_original_query()
                state = "exit"
            elif choice == 'p':
                self._rerun_last_plan()
                state = "exit"
            elif choice == 'f':
                self._add_feedback_and_regenerate()
                state = "exit"
            elif choice == 'x':
                self._show_xml_plan()
            elif choice == 's':
                self._save_job_cli()
            elif choice == 'exit':
                state = "exit"
            else:
                logging.info("Invalid option")

        # Clear state after processing
        self.agent.job_queue = []
        self.agent.outputs = {}
        self.initial_query = None

    def _rerun_original_query(self):
        """Reruns the original query."""
        self.agent = Agent()
        self.agent.add_job(f"""<actions>
            <action id="0" type="reasoning">
                <content>Generate an XML action plan to: {self.initial_query}</content>
            </action>
        </actions>""")
        self.agent.process_queue()
        self._handle_response()

    def _rerun_last_plan(self):
        """Reruns the last generated plan if available."""
        if self.last_generated_plan_xml:
            logging.info("\n🔄 Re-running last plan...")
            self.agent = Agent()
            self.agent.add_job(self.last_generated_plan_xml)
            self.agent.process_queue()
            self._show_results()
        else:
            logging.info("No plan stored to rerun")

    def _add_feedback_and_regenerate(self):
        """Adds user feedback and regenerates the plan."""
        feedback = self.user_input_fn("Enter feedback: ").strip()
        if feedback:
            self.feedback_history.append(feedback)
            logging.info("\n🔄 Regenerating with feedback...")
            # Limit feedback history to the last 3 messages
            self.feedback_history = self.feedback_history[-3:]
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

    def _show_xml_plan(self):
        """Displays the last generated XML plan."""
        if self.last_generated_plan_xml:
            logging.info("\n📦 Stored XML Plan:\n" + "="*50)
            logging.info(self.last_generated_plan_xml)
            logging.info("="*50)
        else:
            logging.info("No XML plan stored")

    