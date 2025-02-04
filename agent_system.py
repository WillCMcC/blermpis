from dataclasses import dataclass
import xml.etree.ElementTree as ET
import subprocess
import os
import warnings
from cmd import Cmd
from openai import OpenAI

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
                        client = OpenAI(
                            api_key=os.getenv("DEEPSEEK_API_KEY"),
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
                    print(f"\n⚠️ Job {job.id} failed: {e}")  # Add error visibility

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
                root = ET.fromstring(last_output['content'])
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
            if job.status == 'completed':
                print(f"- {job.id}: {self.agent.outputs.get(job.id, 'No output')}")
            elif job.status.startswith('failed'):
                print(f"- {job.id}: FAILED - {job.status.split(':')[-1].strip()}")
    
    def do_exit(self, arg):
        """Exit the CLI"""
        return True

if __name__ == '__main__':
    AgentCLI().cmdloop()
