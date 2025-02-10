```markdown
# AI-Powered Command-Line Interface

## Introduction
This project implements an AI-powered command-line interface (CLI) that uses natural language to generate and execute complex task workflows. It combines OpenAI's language models with a flexible job execution system to break down user requests into actionable steps that can include Python code execution, shell commands, and AI reasoning tasks.

## Purpose
The project addresses several key challenges in task automation and AI interaction:

1. **Natural Language Task Execution**: It bridges the gap between natural language task descriptions and executable code by automatically generating structured action plans.

2. **Workflow Automation**: Complex tasks are automatically broken down into logical, executable steps with proper dependency management.

3. **Interactive Development**: The system provides immediate feedback and allows users to iterate on generated plans through an interactive CLI interface.

4. **Code Generation and Execution**: Users can generate and execute Python scripts and shell commands without directly writing code, while maintaining full visibility and control over the execution process.

The system serves as a practical interface between natural language intent and concrete computational tasks, making it easier for users to accomplish complex programming and automation tasks through conversation.

## Files

### prompts.py
Contains the core prompt definitions and examples used throughout the system. It defines:
- `INITIAL_SYSTEM_PROMPT`: The base prompt that guides the AI planner in generating XML action plans, including core principles, JSON usage guidelines, and data flow rules
- `PLANNING_EXAMPLES`: A collection of example interactions showing how to generate XML action plans for various tasks
- `JSON_SYSTEM_PROMPT`: Instructions for generating valid JSON responses
- `CONTENT_SYSTEM_PROMPT`: Guidelines for content generation and formatting

### agent.py 
The core execution engine that implements the Agent class. This file:
- Manages the job queue and job execution
- Processes different job types (bash, python, reasoning, input)
- Handles dependencies between jobs
- Interacts with the OpenAI API for AI reasoning tasks
- Implements error handling and status tracking
- Manages output collection and storage

### cli.py
The command-line interface for interacting with the Agent system. Key features:
- Provides a natural language interface for submitting queries
- Handles job execution and displays results
- Manages saved jobs and command history
- Implements post-execution options (rerun, regenerate, feedback)
- Provides tools for debugging and viewing generated XML plans
- Manages the history of user interactions and feedback

## Usage

The CLI provides an interactive interface for executing tasks using natural language. Here's how to use it:

### Basic Usage

1. Start the CLI:
```bash
python cli.py
```

2. Enter your task in natural language at the prompt:
```bash
agent> create a python script that prints hello world
```

3. Review the generated plan and choose an option:
- `y` - Execute the plan
- `n` - Cancel execution 
- `R` - Regenerate the plan
- `!` - Add feedback and regenerate

### Loading and Executing Saved Jobs

List available saved jobs:
```bash
agent> j
```

Execute a specific saved job:
```bash
agent> job:myjob
```

### Post-Execution Options

After plan execution, several options are available:

```bash
Post-execution options:
  q - Rerun original query
  p - Rerun last plan  
  f - Add feedback & regenerate
  x - Show generated XML plan
  s - Save current job plan
  exit - Return to prompt
```

To save a successful plan:
```bash
agent(post)> s
Enter name to save job as: myjob
```

To view the generated XML:
```bash
agent(post)> x
```

To add feedback and regenerate:
```bash
agent(post)> f
Enter feedback: Make the output more verbose
```

### Command History

The CLI maintains command history across sessions. Use the up/down arrow keys to access previous commands. History is automatically saved to `.agent_prompt_history`.

### Error Handling

If a job fails, additional debugging options become available:
```bash
Post-execution options:
  f - Add feedback & debug failure
  x - Show generated XML plan 
  s - Save current job plan
  exit - Return to prompt
```

The CLI will display detailed error information and allow you to add feedback to improve the plan.

## Dependencies

This project requires the following Python packages:

```bash
pip install openai beautifulsoup4 requests python-dotenv
```

This project requires an Openrouter API key. You can get one by signing up at [Openrouter](https://openrouter.ai/).

Create a `.env` file in the project root directory with the following content:

```bash
OPENROUTER_API_KEY = "YOUR_API_KEY_HERE"
```

### Required Packages:
- **openai**: For interacting with the OpenAI/OpenRouter API
- **beautifulsoup4**: For parsing XML and HTML content 
- **requests**: For making HTTP requests
- **python-dotenv**: For loading environment variables from .env files

The standard library modules `xml.etree.ElementTree`, `cmd`, `readline`, `json`, and `pathlib` are also used but don't require separate installation as they are included with Python.

To install all dependencies at once, you can run:

```bash
pip install -r requirements.txt
```

Note: Make sure you have Python 3.6 or higher installed on your system.
```