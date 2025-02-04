from dataclasses import dataclass
import xml.etree.ElementTree as ET
import subprocess
import os
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

class Agent:
    def __init__(self):
        self.job_queue = []
        self.outputs = {}  # Stores results of completed jobs
        
    def add_job(self, action_xml: str):
        root = ET.fromstring(action_xml)
        for action in root.findall('action'):
            job_id = action.get('id')
            job_type = action.get('type')
            content = action.find('content').text.strip()
            depends_on = action.get('depends_on', '').split(',') if action.get('depends_on') else []
            
            self.job_queue.append(Job(
                id=job_id,
                type=job_type,
                content=content,
                depends_on=depends_on
            ))
    
    def process_queue(self):
        for job in self.job_queue:
            if all(self.outputs.get(dep) is not None for dep in job.depends_on):
                try:
                    if job.type == 'bash':
                        result = subprocess.run(job.content, shell=True, capture_output=True, text=True)
                        self.outputs[job.id] = result.stdout
                    elif job.type == 'python':
                        exec(job.content, globals(), self.outputs)
                    elif job.type == 'reasoning':
                        api_key = DEEPSEEK_API_KEY
                        if not api_key:
                            raise ValueError("DEEPSEEK_API_KEY environment variable not set")
                            
                        client = OpenAI(
                            api_key=api_key,
                            base_url="https://api.deepseek.com"
                        )
                        with warnings.catch_warnings():
                            warnings.simplefilter("ignore")
                            response = client.chat.completions.create(
                                model="deepseek-reasoner",
                                messages=[
                                    {"role": "system", "content": """First think step-by-step, then output XML actions.
Respond ONLY with valid XML using:
<bash> - Terminal commands
<python> - Code execution
<reasoning> - Analysis steps
<request_input> - Get user input"""},
                                    {"role": "user", "content": job.content}
                                ],
                                max_tokens=4096,
                                stream=False
                            )
                        
                        # Store both CoT and final answer
                        self.outputs[job.id] = {
                            'reasoning': response.choices[0].message.content,
                            'content': response.choices[0].message.content
                        }
                    job.status = 'completed'
                except Exception as e:
                    job.status = f'failed: {str(e)}'
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
    
    def default(self, line):
        """Handle natural language queries"""
        if self.initial_query is None:
            self.initial_query = line
            # Create first reasoning job
            self.agent.add_job(f"""<actions>
                <action id="0" type="reasoning">
                    <content>{line}</content>
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
            print("\n[Chain of Thought]")
            print(last_output['reasoning'])
            
            print("\n[Proposed Actions]")
            print(last_output['content'])
            
            try:
                response_content = last_output['content']
                
                # Fix: Normalize XML structure if needed
                if not response_content.startswith('<actions>'):
                    # Wrap standalone commands in <actions><action> structure
                    root = ET.fromstring(f"<wrapper>{response_content}</wrapper>")
                    normalized = ET.Element('actions')
                    action_id = 1  # Start after initial 0 job
                    
                    for elem in root:
                        action = ET.SubElement(normalized, 'action', {
                            'id': str(action_id),
                            'type': elem.tag
                        })
                        ET.SubElement(action, 'content').text = elem.text
                        action_id += 1
                    
                    response_content = ET.tostring(normalized, encoding='unicode')
                
                root = ET.fromstring(response_content)
                # Handle input requests first
                inputs = {}
                for req in root.findall('request_input'):
                    input_id = req.get('id')
                    prompt = req.find('prompt').text
                    inputs[input_id] = input(prompt + " ")
                
                # Add collected inputs to outputs
                self.agent.outputs.update(inputs)
                
                # Now handle actions
                action_xml = ET.tostring(root, encoding='unicode')
                self.agent.add_job(action_xml)
                
                # Get user confirmation
                print("\n[Generated Plan]")
                for job in self.agent.job_queue:
                    if job.status == 'pending':
                        print(f"{job.id}: {job.type} - {job.content[:50]}...")
                
                if input("Execute these actions? (y/n) ").lower() == 'y':
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
        """Display job outcomes"""
        print("\n[Execution Results]")
        for job in self.agent.job_queue:
            output = self.agent.outputs.get(job.id, 'No output')
            if job.type == 'bash':
                output = f"\n💻 Command: {job.content}\n📋 Output:\n{output}"
            elif job.type == 'python':
                output = f"\n🐍 Code executed: {job.content[:100]}...\n📦 Result: {output}"
                
            if job.status == 'completed':
                print(f"\n✅ {job.id} ({job.type}): {output}")
            elif job.status.startswith('failed'):
                print(f"\n❌ {job.id} ({job.type}): FAILED - {job.status.split(':')[-1].strip()}")
    
    def do_exit(self, arg):
        """Exit the CLI"""
        return True

if __name__ == '__main__':
    AgentCLI().cmdloop()
