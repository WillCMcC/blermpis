from dataclasses import dataclass
import xml.etree.ElementTree as ET
import subprocess
import os
import re
import warnings
from cmd import Cmd
from openai import OpenAI

DEEPSEEK_API_KEY='sk-c4e470b3ca36497d87cabd72c79b4fcf'

@dataclass
class Job:
    id: str
    type: str  # 'bash', 'python', or 'reasoning'
    content: str
    depends_on: list[str] = None
    status: str = 'pending'
    output_ref: str = None  # Variable name to store output in

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
            
            self.job_queue.append(Job(
                id=job_id,
                type=job_type,
                content=content,
                depends_on=depends_on
            ))
    
    def process_queue(self):
        # Process a copy to safely modify original list
        for job in list(self.job_queue):  # Iterate copy of queue
            if job.status != 'pending':
                continue
            if all(self.outputs.get(dep) is not None for dep in job.depends_on):
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
                            locs = {
                                'outputs': {dep: self.outputs.get(dep) for dep in job.depends_on}
                            }
                            exec(job.content, globals(), locs)
                            output = buffer.getvalue()
                            # Store both variables and output
                            self.outputs[job.id] = {
                                'output': output,
                                'variables': locs
                            }
                        except Exception as e:
                            output = f"Python Error: {str(e)}"
                            self.outputs[job.id] = output
                        finally:
                            sys.stdout = old_stdout
                    elif job.type == 'reasoning':
                        # Add query logging
                        print(f"\n[Reasoning Query]\n{job.content}\n{'='*50}")

                        api_key = DEEPSEEK_API_KEY
                        if not api_key:
                            raise ValueError("DEEPSEEK_API_KEY environment variable not set")
                            
                        client = OpenAI(
                            api_key=api_key,
                            base_url="https://api.deepseek.com"
                        )
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            # Determine system message based on job type
                            if job.id == "0":  # Initial planning job
                                system_msg = """You are an AI planner. Generate XML action plans with these requirements:
1. All outputs are stored in variables named outputs["JOB_ID"]
2. Python scripts access previous results via outputs parameter
3. Bash commands access previous results via $OUTPUT_JOB_ID variables
4. Reasoning steps reference previous outputs as {{outputs.JOB_ID}}
Wrap ALL steps in <actions> tags. Use depends_on attributes for dependencies."""
                            else:  # Subsequent reasoning queries
                                system_msg = """You are a helpful assistant. Provide a concise response wrapped in <response> tags."""

                            response = client.chat.completions.create(
                                model="deepseek-reasoner",
                                messages=[
                                    {"role": "system", "content": system_msg},
                                    {"role": "user", "content": job.content}
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
            
        if last_output:
            response_content = last_output['raw_response']
                
            # Handle direct responses from non-planning reasoning jobs
            if last_output['response_type'] == 'content':
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
                return
                
            print("\n[Proposed Actions]")
            print(response_content)
            
            try:
                
                # Fix: Normalize XML structure if needed
                if not response_content.startswith('<actions>'):
                    # Wrap standalone commands in <actions><action> structure
                    root = ET.fromstring(f"<wrapper>{response_content}</wrapper>")
                    normalized = ET.Element('actions')
                    action_id = 1  # Start after initial 0 job
                    
                    for elem in root:
                        if elem.tag == 'request_input':  # Only skip input requests
                            continue
                        action = ET.SubElement(normalized, 'action', {
                            'id': str(action_id),
                            'type': elem.tag,
                            'desc': elem.get('desc', '')
                        })
                        # Add output reference handling
                        if elem.tag in ['python', 'bash']:
                            action.set('output_ref', f'result_{action_id}')
                        content = ET.SubElement(action, 'content')
                        content.text = elem.text
                        action_id += 1
                    
                    response_content = ET.tostring(normalized, encoding='unicode')
                
                root = ET.fromstring(response_content)
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
                        prefix = "🖥️  BASH" if job.type == 'bash' else "🐍 PYTHON"
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
                print(last_output)
        else:
            print("No response received")

    def _show_results(self):
        """Display job outcomes with improved formatting"""
        print("\n" + "="*50 + "\n📊 Execution Results\n" + "="*50)
        for job in self.agent.job_queue:
            if job.id == "0":  # Skip initial reasoning job
                continue
                
            result = self.agent.outputs.get(job.id, 'No output recorded')
            
            header = f"\n🔹 [{job.type.upper()} JOB {job.id}]"
            command = f"\n⚡ Command:\n{job.content}" if job.type == 'bash' else ""
            
            if job.type == 'python':
                output = f"\n🐍 Output:\n{result.get('output', 'No print output')}" if isinstance(result, dict) else f"\n❌ Error:\n{result}"
                command = f"\n📜 Script:\n{job.content}"
            elif job.type == 'bash':
                output = f"\n📤 Output:\n{result}" if result else "✅ Command executed successfully"

            print(f"{header}{command}{output}")
            
            if job.status.startswith('failed'):
                print(f"\n🔥 Failure: {job.status.split(':', 1)[-1].strip()}")
    
    def do_exit(self, arg):
        """Exit the CLI"""
        return True

if __name__ == '__main__':
    AgentCLI().cmdloop()
