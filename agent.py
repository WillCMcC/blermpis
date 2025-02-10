import time
import os
import shutil
from pathlib import Path
from dataclasses import dataclass
import xml.etree.ElementTree as ET
import subprocess
import re
import warnings
import json
import requests
from openai import OpenAI
from prompts import INITIAL_SYSTEM_PROMPT, PLANNING_EXAMPLES, JSON_SYSTEM_PROMPT, CONTENT_SYSTEM_PROMPT

DEEPSEEK_API_KEY='sk-c4e470b3ca36497d87cabd72c79b4fcf'
OPENROUTER_API_KEY='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'
GROQ_API_KEY='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'

OPENROUTER_API_URL='https://openrouter.ai/api/v1'
GROQ_API_URL='https://api.groq.com/openai/v1'
DEEPSEEK_API_URL='https://api.deepseek.com'

@dataclass
class Job:
    id: str
    type: str  # 'bash', 'python', or 'reasoning'
    content: str
    depends_on: list[str] = None
    status: str = 'pending'
    output_ref: str = None  # Variable name to store output in
    model: str = None  # Add model parameter
    response_format: str = None  # Add response format field

class Agent:
    def __init__(self):
        self.job_queue = []
        self.outputs = {}  # Stores results of completed jobs
        self.output_buffer = []  # Global output accumulator
        
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
            template_deps = re.findall(r'\{\{outputs\.([\w]+)[^\}]*\}\}', content)  # Capture only job ID before first dot
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
                    warnings.warn(f"Ignoring model/format on {job_type} job {job_id}")
            self.job_queue.append(Job(
                id=job_id,
                type=job_type,
                content=content,
                depends_on=depends_on,
                model=model,  # Add model parameter
                response_format=response_format  # Store response format
            ))

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
                    elif job.type == 'python':
                        # Capture printed output
                        import sys
                        from io import StringIO
                        old_stdout = sys.stdout
                        sys.stdout = buffer = StringIO()
                        
                        try:
                            locs = {
                                'outputs': {dep: self.outputs.get(dep) for dep in job.depends_on},
                                'agent': self,  # Add agent reference
                                'get_output': lambda key: self.outputs.get(key),
                                'json': json,  # Make json available to scripts
                                'requests': requests,  # Make request available to scripts
                                'append_output': lambda text: self.output_buffer.append(str(text)),
                                'validate_json': lambda data, keys: all(k in data for k in keys),
                                'get_json_field': lambda job_id, field: self.outputs.get(job_id, {}).get('response_json', {}).get('content', {}).get(field)
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
                        except Exception as e:
                            error_msg = str(e)
                            # Add specific JSON handling
                            if "response_json" in error_msg:
                                error_msg += "\n🔍 JSON ISSUE - Possible fixes:" \
                                           "\n1. Verify previous job uses format=\"json\"" \
                                           "\n2. Check outputs[\"ID\"][\"json_error\"] for parsing issues" \
                                           "\n3. Access fields via outputs[\"ID\"][\"response_json\"][\"content\"]"
                            
                            # Add dependency check
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
                    elif job.type == 'input':
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
                        
                    elif job.type == 'reasoning':
                        # Add query logging
                        print(f"[🤖] Running reasoning job '{job.id}' with model {job.model}")

                        api_key = OPENROUTER_API_KEY
                        if not api_key:
                            raise ValueError("DEEPSEEK_API_KEY environment variable not set")
                            
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

                            # Use job-specific model if specified, else deepseek-r1
                            # Force deepseek-r1 for initial planning job
                            model = job.model or 'google/gemini-2.0-flash-001'
                            # model = 'google/gemini-2.0-flash-001'
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
                        if not response_content:
                            raise ValueError("Empty response from API - check model permissions/availability")
                        if response.choices[0].finish_reason != 'stop':
                            print(f"\n⚠️ API WARNING: Finish reason '{response.choices[0].finish_reason}'")
                            print(f"   Token Usage: {response.usage}")
                        response_content = response.choices[0].message.content
                        
                        # Add response logging before storing
                        # print(f"[Raw Reasoning Response]\n{response_content}\n{'='*50}")
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
