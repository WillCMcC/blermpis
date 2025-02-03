from dataclasses import dataclass
import xml.etree.ElementTree as ET
import subprocess
import os
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
                        response = client.chat.completions.create(
                            model="deepseek-chat",
                            messages=[
                                {"role": "system", "content": """You are an autonomous agent that solves tasks using XML-formatted actions. 

Respond ONLY with valid XML using these actions:
<bash> - Run terminal commands
<python> - Execute Python code 
<reasoning> - Perform analysis or break down steps

For "get reddit posts and save to JSON" you might respond:
<actions>
  <action id="1" type="bash">
    <content>pip install praw</content>
  </action>
  <action id="2" type="python" depends_on="1">
    <content>
    import praw, json
    # ...reddit API code...
    </content>
  </action>
  <action id="3" type="reasoning" depends_on="2">
    <content>Analyze the collected data and summarize key trends</content>
  </action>
</actions>

Guidelines:
1. Always start with required setup steps
2. Chain dependencies properly
3. Validate commands before suggesting
4. Prefer Python over bash for complex tasks"""},
                                {"role": "user", "content": job.content}
                            ],
                            stream=False
                        )
                        self.outputs[job.id] = response.choices[0].message.content
                    job.status = 'completed'
                except Exception as e:
                    job.status = f'failed: {str(e)}'

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
        last_output = self.agent.outputs.get("0")
        if last_output:
            print("\n[Agent Response]")
            print(last_output)
            
            # Check if agent generated new XML actions
            try:
                ET.fromstring(last_output)
                self.agent.add_job(last_output)
                print("\n[Executing Generated Plan]")
                self.agent.process_queue()
                self._show_results()
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
