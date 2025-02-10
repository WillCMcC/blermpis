from cmd import Cmd
from pathlib import Path
import xml.etree.ElementTree as ET
from agent import (
    Agent,
    Job,
    DEEPSEEK_API_KEY,
    OPENROUTER_API_KEY,
    OPENROUTER_API_URL
)

class AgentCLI(Cmd):
    prompt = 'agent> '
    
    def __init__(self):
        super().__init__()
        self.agent = Agent()
        self.initial_query = None
        self.feedback_history = []  # Track feedback across regenerations
        self.last_generated_plan_xml = None  # Store original XML for recall
        
        # Show welcome message
        print("\n🤖 Welcome to AgentCLI!")
        print("\nQuick Start:")
        print("1. Type your request in natural language")
        print("2. Type 'j' to list and run saved jobs")
        print("3. Type 'exit' to quit")
        print("\nExample: create a python script that prints hello world")
        print("="*50)
    
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
            <action id="1" type="reasoning" depends_on="0" model="google/gemini-2.0-flash-001">
                <content>
                    Review and improve this plan for efficiency and correctness:
                    {{{{outputs.0.raw_response}}}}
                    
                    Consider:
                    1. Are dependencies properly declared?
                    2. Are appropriate formats (JSON/text) used?
                    3. Can steps be parallelized?
                    4. Go step by step -- will this plan work?
                    5. Make sure there are no unnecessary steps.
                    6. When in doubt, save to a markdown file with a timestamp as the final job.
                    
                    Return ONLY the improved XML plan.
                </content>
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
        initial_job = next((job for job in self.agent.job_queue if job.id == "1"), None)
        
        if initial_job and initial_job.status.startswith('failed'):
            print(f"\n❌ Initial processing failed: {initial_job.status.split(':', 1)[-1].strip()}")
            return
                
        last_output = self.agent.outputs.get("1")
        
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
                    self._save_job(job_name)
            elif choice == 'exit':
                break
            else:
                print("Invalid option")

        # Clear state after processing
        self.agent.job_queue = []
        self.agent.outputs = {}
        self.initial_query = None
    
    def _save_job(self, job_name: str):
        """Save the current XML plan to jobs directory"""
        jobs_dir = Path("jobs")
        jobs_dir.mkdir(exist_ok=True)
        
        if not self.last_generated_plan_xml:
            print("No plan to save")
            return
            
        dest = jobs_dir / f"{job_name}.xml"
        dest.write_text(self.last_generated_plan_xml, encoding='utf-8')
        print(f"✅ Saved job to {dest}")

    def _load_job(self, job_name: str) -> str:
        """Load XML from jobs directory"""
        job_path = Path("jobs") / f"{job_name}.xml"
        if not job_path.exists():
            raise FileNotFoundError(f"Job {job_name} not found")
        return job_path.read_text(encoding='utf-8')

    def do_j(self, arg):
        """List and select available jobs"""
        jobs_dir = Path("jobs")
        if not jobs_dir.exists() or not list(jobs_dir.glob("*.xml")):
            print("No saved jobs found")
            return

        # List available jobs
        print("\nAvailable jobs:")
        jobs = list(jobs_dir.glob("*.xml"))
        for i, job_path in enumerate(jobs, 1):
            job_name = job_path.stem
            print(f"{i}. {job_name}")

        # Get user selection
        while True:
            choice = input("\nSelect job number (or 'exit'): ").strip()
            if choice.lower() == 'exit':
                return
            try:
                idx = int(choice) - 1
                if 0 <= idx < len(jobs):
                    selected_job = jobs[idx].stem
                    print(f"\n📂 Executing saved job: {selected_job}")
                    xml_content = self._load_job(selected_job)
                    self.agent = Agent()
                    self.agent.add_job(xml_content)
                    self.last_generated_plan_xml = xml_content
                    self.agent.process_queue()
                    self._show_results()
                    break
                else:
                    print("Invalid job number")
            except ValueError:
                print("Please enter a valid number")

    def do_exit(self, arg):
        """Exit the CLI"""
        return True

if __name__ == '__main__':
    AgentCLI().cmdloop()
