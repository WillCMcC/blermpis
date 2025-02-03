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
                                {"role": "system", "content": """You are an AI agent that can execute tasks using different tools. 
You MUST respond with XML-formatted actions using these tools:

Available Tools:
1. <action type="bash"> - Execute bash commands
2. <action type="python"> - Run Python code (use print() for output)
3. <action type="reasoning"> - Get AI reasoning (uses same prompt format)

XML Format:
<actions>
  <action id="unique_id" type="tool_type" depends_on="comma_separated_ids">
    <content>Task content</content>
  </action>
  ...
</actions>

Guidelines:
- Use depends_on to chain actions (wait for previous results)
- Access outputs using outputs['previous_id']
- Always validate commands before execution
- Include multiple actions when needed"""},
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
    
    def do_submit(self, arg):
        """Submit XML actions from a file: submit filename.xml"""
        with open(arg, 'r') as f:
            self.agent.add_job(f.read())
        print(f"Added jobs from {arg}")
    
    def do_queue(self, arg):
        """Show current job queue"""
        for job in self.agent.job_queue:
            deps = ','.join(job.depends_on) if job.depends_on else 'none'
            print(f"{job.id}: {job.type} (deps: {deps}) - {job.status}")
    
    def do_run(self, arg):
        """Process the job queue"""
        self.agent.process_queue()
        print("Queue processing completed")
    
    def do_exit(self, arg):
        """Exit the CLI"""
        return True

if __name__ == '__main__':
    AgentCLI().cmdloop()
