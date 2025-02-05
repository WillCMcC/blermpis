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

@dataclass
class Job:
    id: str
    type: str  # 'bash', 'python', or 'reasoning'
    content: str
    depends_on: list[str] = None
    status: str = 'pending'
    output_ref: str = None  # Variable name to store output in
    model: str = None  # Add model parameter

class Agent:
    def __init__(self):
        self.job_queue = []
        self.outputs = {}  # Stores results of completed jobs
        
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
            template_deps = re.findall(r'\{\{outputs\.(\d+)\}\}', content)
            depends_on += template_deps
            
            # Remove duplicates and empty strings
            depends_on = list(set([d for d in depends_on if d]))
            
            # Add model parameter extraction
            model = action.get('model')  # Get model if specified
            if job_type == 'reasoning' and not model:
                model = 'deepseek/deepseek-r1'  # Default for reasoning

            self.job_queue.append(Job(
                id=job_id,
                type=job_type,
                content=content,
                depends_on=depends_on,
                model=model  # Add model parameter
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
                        self.outputs[job.id] = clean_output.strip()
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
                                'json': json  # Make json available to scripts
                            }
                            exec(job.content, globals(), locs)
                            output = buffer.getvalue()
                            # Store both variables and output
                            self.outputs[job.id] = {
                                'output': output,
                                'variables': locs
                            }
                        except Exception as e:
                            error_msg = str(e)
                            if "KeyError" in error_msg:
                                error_msg += "\n🔑 Missing dependency - Verify:"
                                error_msg += "\n1. All referenced jobs are in depends_on"
                                error_msg += "\n2. Previous jobs completed successfully"
                            output = f"Python Error: {error_msg}"
                            self.outputs[job.id] = {
                                'error': output,
                                'output': output  # Maintain output key for compatibility
                            }
                        finally:
                            sys.stdout = old_stdout
                    elif job.type == 'reasoning':
                        # Add query logging
                        print(f"\n[Reasoning Query]\n{job.content}\n{'='*50}")

                        api_key = OPENROUTER_API_KEY
                        if not api_key:
                            raise ValueError("DEEPSEEK_API_KEY environment variable not set")
                            
                        client = OpenAI(
                            api_key=GROQ_API_KEY,
                            base_url=GROQ_API_URL,
                        )
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            # Determine system message based on job type
                            if job.id == "0":  # Initial planning job
                                system_msg = """You are an AI planner. Generate XML action plans with these requirements:
1. First action (id=0) MUST use model="deepseek/deepseek-r1"
2. Subsequent actions can specify models:
   - deepseek/deepseek-r1: slow general reasoning (default)
   - openai/o1-mini: fast general reasoning 
   - anthropic/claude-3.5-sonnet: Complex analysis/long-form content
   - meta-llama/llama-3.1-405b-instruct: Creative writing
3. Strict XML Formatting:
   - NEVER use markdown code blocks (```xml) 
   - ALWAYS start with <?xml version="1.0"?> as first line
   - Remove ALL markdown formatting from XML
   - Ensure proper XML escaping for special characters
   - Validate XML structure before responding
4. XML Structure:
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
5. Response Formats:
   - Reasoning jobs return either:
     * XML <actions> plan for subsequent steps
     * JSON data for direct answers (wrap in <response>)
   - XML takes precedence if detected
6. Error Handling:
   - Validate JSON parsing in Python
   - Declare ALL dependencies in depends_on
   - Handle missing dependencies explicitly
   - REJECT any plan that doesn't explicitly declare ALL data dependencies

Example valid structure with proper data flow:
<?xml version="1.0"?>
<actions>
  
  <!-- Planning job with explicit input dependency -->
  <action type="reasoning" id="plan" model="deepseek/deepseek-r1" depends_on="priority">
    <content>Create outline focused on technical aspects...</content>
  </action>

  <!-- Content generation with proper dependency chain -->
  <action type="reasoning" id="1" model="anthropic/claude-3.5-sonnet" depends_on="plan">
    <content>Expand {{outputs.plan.raw_response}} into detailed analysis...</content>
  </action>

  <!-- Python processing with proper output handling -->
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

  <!-- Final output with proper dependency -->
  <action type="bash" id="3" depends_on="2">
    <content>echo "Analysis complete: $OUTPUT_2"</content>
  </action>
</actions>"""
                            else:  # Subsequent reasoning queries
                                system_msg = """You are a helpful assistant. Provide a concise response wrapped in <response> tags."""

                            # Substitute template variables
                            from string import Template
                            job_content = Template(job.content).safe_substitute({
                                f'outputs.{dep_id}': str(self.outputs.get(dep_id, {}).get('raw_response', ''))
                                for dep_id in job.depends_on
                            })

                            # Use job-specific model if specified, else deepseek-r1
                            # Force deepseek-r1 for initial planning job
                            # model = job.model or 'deepseek/deepseek-r1'
                            model = 'llama-3.3-70b-specdec'
                            response = client.chat.completions.create(
                                model=model,
                                messages=[
                                    {"role": "system", "content": system_msg},
                                    {"role": "user", "content": job_content}
                                ],
                                max_tokens=4096,
                                stream=False
                            )
                        response_content = response.choices[0].message.content
                        
                        # Add response logging before storing
                        print(f"[Raw Reasoning Response]\n{response_content}\n{'='*50}")
                        # Store response with type information
                        self.outputs[job.id] = {
                            'raw_response': response_content,
                            'response_type': 'actions' if '<actions>' in response_content else 'content'
                        }
                    job.status = 'completed'
                except Exception as e:
                    job.status = f'failed: {str(e)}'
                    # Remove from queue whether successful or failed
                    self.job_queue.remove(job)
                    # Improve error parsing for API responses
                    if "401" in str(e):
                        print("\n🔑 Authentication Failed - Verify:")
                        print("1. You have a valid DeepSeek API key")
                        print("2. Your API key is set in environment:")
                        print("   export DEEPSEEK_API_KEY=your_key_here")
                    else:
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
            
        if self.initial_query is None:
            self.initial_query = line
            # Create first reasoning job
            self.agent.add_job(f"""<actions>
                <action id="0" type="reasoning">
                    <content>Generate an XML action plan to: {line}</content>
                </action>
            </actions>""")
            self.agent.process_queue()
            self._handle_response()
        else:
            print("Continue with your query or 'exit' to quit")
    def _handle_response(self):
        #Process and display results
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
                        prefix = "🖥️  BASH" if job.type == 'bash' else \
                                "🐍 PYTHON" if job.type == 'python' else \
                                "💭 REASONING"
                        print(f"\n{prefix} ACTION [ID {job.id}]:")
                        print("-"*40)
                        print(job.content)
                        print("-"*40)

                if input("\n🚀 Execute these actions? (y/n) ").lower() == 'y':
                    print("\n[Executing Generated Plan]")
                    self.agent.process_queue()
                    self._show_results()
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
        """Display job outcomes with improved formatting"""
        print("\n" + "="*50 + "\n📊 Execution Results\n" + "="*50)
        for job in self.agent.job_queue:
            if job.id == "0":  # Skip initial reasoning job
                continue
                
            result = self.agent.outputs.get(job.id, {})  # Default to empty dict
            output = ""  # Initialize output variable
            
            header = f"\n🔹 [{job.type.upper()} JOB {job.id}]"
            command = ""

            # Handle different result types safely
            if isinstance(result, dict):
                if job.type == 'python':
                    output = f"\n🐍 Output:\n{result.get('output', 'No print output')}"
                    command = f"\n📜 Script:\n{job.content}"
                elif job.type == 'bash':
                    output = f"\n📤 Output:\n{result.get('output', '')}" 
                    command = f"\n⚡ Command:\n{job.content}"
                else:  # Explicit handling for reasoning jobs
                    response = result.get('raw_response', 'No response captured')
                    output = f"\n💭 Response:\n{response}"
            else:  # Handle legacy string outputs
                output = f"\n⚠️ Raw Output:\n{result}"
                
            print(f"{header}{command}{output}")
            
            if job.status.startswith('failed'):
                print(f"\n🔥 Failure: {job.status.split(':', 1)[-1].strip()}")
    
    def do_exit(self, arg):
        """Exit the CLI"""
        return True

if __name__ == '__main__':
    AgentCLI().cmdloop()
