INITIAL_SYSTEM_PROMPT = """You are an AI planner. Generate XML action plans with these guidelines:

CORE PRINCIPLES:
1. Prefer simple, linear workflows unless complexity is required
2. Use appropriate formats for data interchange:
   - JSON when Python code needs structured data
   - Raw text for human-readable outputs
3. Ensure clear dependency declarations

JSON USAGE GUIDELINES (use only when needed):
- Consider format="json" when:
  * Output requires specific field names
  * Data will be processed programmatically
  * Exact structure validation is critical
  * if a script depends on a reasoning job, that reasoning job MUST be of type JSON
  * ensure JSON content includes only necessary properties

DATA FLOW RULES:
- All data MUST flow through declared dependencies
- Access outputs through:
  * Bash: $OUTPUT_ID
  * Python: outputs["ID"]["raw_response"] 
  * Reasoning: {{outputs.ID.raw_response}}

Action Types:
   - reasoning: AI model processing with optional format="json"
   - python: Execute Python code
   - bash: Execute shell commands
   - input: Request user input with optional prompt

Actions can specify models:
   - google/gemini-2.0-flash-001: reasoning, largest context window for long document polishing
   - openai/gpt-4o: best at trivia and general knowledge 
   - openai/o1-mini: fast general reasoning 
   - anthropic/claude-3.5-sonnet: creative writing and poetry

When asked to produce a document, use the reasoning model to generate an outline 
    - following steps can reference these outlines to fill them in piece by piece
    - Prioritize making multiple calls when asked to generate long form content. Aim for chunks of 1000-2000 words maximum
    - Ensure steps conform to defined data access patterns -- semantic requests for data will not be fulfilled"""

PLANNING_EXAMPLES = [
    {"role": "user", "content": "Generate an XML action plan to: Read your source code at agent_system.py and generate a README.md file for it."},
    {"role": "user", "content": """<?xml version="1.0"?>
<actions>
    <action type="python" id="read_source_code">
        <content>
try:
    with open("agent_system.py", "r") as f:
        source_code = f.read()
    print(source_code)
except FileNotFoundError:
    print("Error: agent_system.py not found.")
except Exception as e:
    print(f"Error reading agent_system.py: {e}")
</content>
    </action>

    <action type="reasoning" id="generate_outline" model="google/gemini-2.0-flash-001" format="json" depends_on="read_source_code">
        <content>
You are an expert software engineer tasked with generating a detailed README for a Python file called "agent_system.py". The source code of the file is provided below. Based on this code, create a JSON outline for a comprehensive README.md file.

The outline should include the following sections, but is not limited to:

*   **Introduction:** A brief overview of what agent_system.py does.
*   **Purpose:** What problems agent_system.py solves
*   **Overview of actions in agent_system.py's system:**
    *   **Action definitions:** Define important constants and datatypes
*   **Setup** How to configure the agent system if applicable
*   **Usage:** Examples of how to use the main functionalities with clear code examples
*   **Dependencies:** List all required Python libraries and how to install them.
*   **Components:** Detailed explanation of each major class and function, including their purpose, arguments, and return values. Focus on function explanations in each component
*   **Error Handling:** How the script handles errors and exceptions.
*   **Future Enhancements:** Potential future improvements and features.
*   **Known Issues:** Any known limitations or bugs.

Ensure the outline is well-structured and hierarchical, with clear headings and subheadings.

Here is the source code of agent_system.py:

```python
{{outputs.read_source_code.raw_response}}
```
</content>
    </action>

    <action type="reasoning" id="generate_introduction" model="google/gemini-2.0-flash-001" depends_on="read_source_code,generate_outline">
        <content>
You are an expert technical writer generating the introduction section for detailed README.md documentation.
Your goal is to create a compelling introduction that explains the what the overall system does.

Here is the source code of agent_system.py:

```python
{{outputs.read_source_code.raw_response}}
```

Here is the outline for the README.md file:
```json
{{outputs.generate_outline.raw_response}}
```

Write the "Introduction" and "Purpose" sections for the README.md, explaining what "agent_system.py" is and its what problem it attempts to solve. Ensure to include markdown formatting.
</content>
    </action>

    <action type="reasoning" id="generate_actiondefinitions" model="google/gemini-2.0-flash-001" depends_on="read_source_code,generate_outline">
        <content>
You are an expert technical writer generating the Overview of actions/Action definitions sections for detailed README.md documentation.

Here is the source code of agent_system.py:

```python
{{outputs.read_source_code.raw_response}}
```

Here is the outline for the README.md file:
```json
{{outputs.generate_outline.raw_response}}
```

Write the "Overview of actions/Action definition" sections based on the source code for the README.md, include definitions of data structures and constants. Ensure to include markdown formatting.
</content>
    </action>

    <action type="reasoning" id="generate_usage" model="google/gemini-2.0-flash-001" depends_on="read_source_code,generate_outline">
        <content>
You are an expert technical writer generating the Usage section for detailed README.md documentation.

Here is the source code of agent_system.py:

```python
{{outputs.read_source_code.raw_response}}
```

Here is the outline for the README.md file:
```json
{{outputs.generate_outline.raw_response}}
```

Write the "Usage" section for the README.md, explaining with illustrative examples how to use the main modules of the system in agent_system.py. Aim for many small but meaningful examples. Ensure to include markdown formatting.
</content>
    </action>

    <action type="reasoning" id="generate_dependencies" model="google/gemini-2.0-flash-001" depends_on="read_source_code,generate_outline">
        <content>
You are an expert technical writer generating the Dependencies section for detailed README.md documentation.

Here is the source code of agent_system.py:

```python
{{outputs.read_source_code.raw_response}}
```

Here is the outline for the README.md file:
```json
{{outputs.generate_outline.raw_response}}
```

Write the "Dependencies" section for the README.md, listing all required Python packages and providing instructions on how to install them using pip. Ensure to include markdown formatting.
</content>
    </action>

    <action type="reasoning" id="generate_components" model="google/gemini-2.0-flash-001" depends_on="read_source_code,generate_outline">
        <content>
You are an expert technical writer generating the Components section for detailed README.md documentation.

Here is the source code of agent_system.py:

```python
{{outputs.read_source_code.raw_response}}
```

Here is the outline for the README.md file:
```json
{{outputs.generate_outline.raw_response}}
```

Write the "Components" section for the README.md, providing a detailed explanation of each class and function in agent_system.py. For each create function section include its purpose, arguments, and return values. Focus on function explanations. Ensure to include markdown formatting.
</content>
    </action>

    <action type="reasoning" id="generate_errorhandling" model="google/gemini-2.0-flash-001" depends_on="read_source_code,generate_outline">
        <content>
You are an expert technical writer generating the Error Handling section for detailed README.md documentation.

Here is the source code of agent_system.py:

```python
{{outputs.read_source_code.raw_response}}
```

Here is the outline for the README.md file:
```json
{{outputs.generate_outline.raw_response}}
```

Write the "Error Handling" section for the README.md, explaining how the system handles errors and exceptions. Ensure to include markdown formatting.
</content>
    </action>

    <action type="reasoning" id="generate_futureenhancements" model="google/gemini-2.0-flash-001" depends_on="read_source_code,generate_outline">
        <content>
You are an expert technical writer generating the "Future Enhancements" section for detailed README.md documentation.

Here is the source code of agent_system.py:

```python
{{outputs.read_source_code.raw_response}}
```

Here is the outline for the README.md file:
```json
{{outputs.generate_outline.raw_response}}
```

Write the "Future Enhancements" section for the README.md, suggesting potential future improvements and features for agent_system.py. Ensure to include markdown formatting.
</content>
    </action>

    <action type="reasoning" id="generate_knownissues" model="google/gemini-2.0-flash-001" depends_on="read_source_code,generate_outline">
        <content>
You are an expert technical writer generating the "Known Issues" section for detailed README.md documentation.

Here is the source code of agent_system.py:

```python
{{outputs.read_source_code.raw_response}}
```

Here is the outline for the README.md file:
```json
{{outputs.generate_outline.raw_response}}
```

Write the "Known Issues" section for the README.md, listing any known limitations or bugs in agent_system.py. If there are no known issues, state that explicitly. Ensure to include markdown formatting.
</content>
    </action>

    <action type="reasoning" id="combine_sections" model="google/gemini-2.0-flash-001" depends_on="generate_introduction,generate_actiondefinitions,generate_usage,generate_dependencies,generate_components,generate_errorhandling,generate_futureenhancements,generate_knownissues">
        <content>
You are an expert software engineer combining individual sections into a complete README.md file.  Ensure the final result is well-formatted and uses markdown.

Here is the Introduction and Purpose:
{{outputs.generate_introduction.raw_response}}

Here are Action definitions:
{{outputs.generate_actiondefinitions.raw_response}}

Here is the Usage section:
{{outputs.generate_usage.raw_response}}

Here is the Dependencies section:
{{outputs.generate_dependencies.raw_response}}

Here is the Components section:
{{outputs.generate_components.raw_response}}

Here is the Error Handling section:
{{outputs.generate_errorhandling.raw_response}}

Here is the Future Enhancements section:
{{outputs.generate_futureenhancements.raw_response}}

Here is the Known Issues section:
{{outputs.generate_knownissues.raw_response}}

Combine these into a well-formatted, complete README.md file.  Pay attention to consistent formatting and a logical flow.
</content>
    </action>

    <action type="python" id="save_readme" depends_on="combine_sections">
        <content>
try:
    readme_content = outputs["combine_sections"]["raw_response"]
    with open("README.md", "w") as f:
        f.write(readme_content)
    print("README.md file created successfully.")
except Exception as e:
    print(f"Error creating README.md: {e}")
</content>
    </action>
</actions>"""},
    {"role": "user", "content": "Generate an XML action plan to: find me deals on Craigslist for PC hardware in Portland, Oregon"},
    {"role": "assistant", "content": """<?xml version="1.0"?>
<actions>
    <action type="bash" id="install_requests">
        <content>pip install requests</content>
    </action>
    
    <action type="python" id="fetch_craigslist" depends_on="install_requests">
        <content>
import requests
import re
import json  # Import the json module

def clean_text(text):
    return re.sub(r'\s+', ' ', text).strip()

def fetch_craigslist_data():
    base_url = f"https://portland.craigslist.org/search/clk/syp"  # syp is computers - system parts
    params = {
        "searchNearby": 1,
        "nearbyArea": "portland",
    }

    try:
        response = requests.get(base_url, params=params, timeout=10) # Add timeout
        if response.status_code == 200:
            
            print((response.content))
            return response.content #Return results for easier testing
        else:
            print(f"Failed to retrieve data. Status code: {response.status_code}")
            return []
    except requests.exceptions.RequestException as e:
        print(f"Request error: {e}")
        return []
    except Exception as e:
        print(f"General error: {e}")
        return []


#No longer needed to print here, since it's being printed inside function
#print(all_results)
print((fetch_craigslist_data()))
</content>
    </action>

    <action type="reasoning" id="analyze_listings_1" model="google/gemini-2.0-flash-001" format="json" depends_on="fetch_craigslist">
        <content>
You are a PC hardware expert analyzing Craigslist listings to identify the best deals in Portland, Oregon (specifically Clark County Craigslist).

Here are the Craigslist listings:
{{outputs.fetch_craigslist.raw_response}}

Analyze the provided listings. Output a JSON object with a "items" array containing the items. For each item, include the "title", "url", "price" as keys. Also include a key containing the number of listings analyzed. 

Leave out any listings that are not for computers or computer parts, or that are obviously no good.

</content>
    </action>

    <action type="reasoning" id="write_markdown_report_1" model="anthropic/claude-3.5-sonnet" depends_on="analyze_listings_1">
        <content>
You are an expert PC hardware reviewer. Based on the following JSON data containing information about craigslist deals, write a markdown report summarizing the best deals. Only provide the Markdown document, no surrounding text. Include at least 10 items.

For each of the top deals from the array, create a section with:

*   The title of the listing as a header.
*   A link to the Craigslist listing.
*   The price.
*   A detailed explanation of why this is a good deal.

Here is the JSON data:
{{outputs.analyze_listings_1.raw_response}}
</content>
    </action>

    <action type="python" id="save_report_1" depends_on="write_markdown_report_1">
        <content>
import datetime

try:
    report = outputs["write_markdown_report_1"]["raw_response"]
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"craigslist_pc_deals_{timestamp}.md"

    with open(filename, "w") as f:
        f.write(report)

    print(f"Report saved to {filename}")
except Exception as e:
    print(f"Error saving report: {e}")
</content>
    </action>
</actions>"""},
    {"role": "user", "content": "Generate an XML action plan to: Help me learn about the linux kernel"},
    {"role": "assistant", "content": """<?xml version="1.0"?>
<actions>
    <action type="reasoning" id="kernel_guide_outline" model="google/gemini-2.0-flash-001" format="json">
        <content>
You are an expert technical writer with deep knowledge of the Linux kernel. Generate a detailed table of contents for a comprehensive guide to the Linux kernel, targeting experienced software engineers and computer science students. The guide should explain the core subsystems and architecture of the kernel. Include a minimum of 10 sections with sub-sections for each. Focus on maintainability and readability. Include the relevant directory structure to explain the major subsystems and their organization.

The structure should be expressed as a JSON object with a "title" and a "sections" array where each section has a "title" and optionally a "subsections" array.  The final section should include a resources section with links to the source code repository to view. Sample format below:

```json
{
  "title": "The Linux Kernel: A Comprehensive Guide",
  "sections": [
    {
      "title": "Introduction to the Linux Kernel",
      "subsections": [
        {
          "title": "What is the Linux Kernel?"
        },
        {
          "title": "Kernel Architecture"
        }
      ]
    },
    {
      "title": "Process Management",
      "subsections":[
        {
          "title": "Scheduling"
        }
      ]
    }
  ]
}
```
        </content>
    </action>

   <action type="reasoning" id="introduction_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Introduction to the Linux Kernel" section. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. The length should be around 1500-2000 words. Use simple language so that the document is easy to read and understand. Do not include code snippets in introduction.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>

   <action type="reasoning" id="process_management_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Process Management" section for the Linux kernel guide. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. Assume that they are well-versed in computer science topics, specifically the Scheduler. The length should be around 1500-2000 words. Reference source code locations when appropriate.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>
    
    <action type="reasoning" id="memory_management_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Memory Management" section for the Linux kernel guide. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. The length should be around 1500-2000 words. Reference source code locations when appropriate, assume they understand fundamental memory management concepts. Explain kernel-specific aspects.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>

     <action type="reasoning" id="virtual_filesystem_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Virtual Filesystem (VFS)" section for the Linux kernel guide. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. The length should be around 1500-2000 words. Reference source code locations when appropriate. Assume they understand fundamental VFS concepts.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>

    <action type="reasoning" id="device_drivers_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Device Drivers" section for the Linux kernel guide. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. The length should be around 1500-2000 words. Reference source code locations when appropriate. Explain the device driver model with the concepts of character and block devices. Explain sysfs, udev.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>

    <action type="reasoning" id="networking_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Networking" section for the Linux kernel guide. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. The length should be around 1500-2000 words. Reference source code locations when appropriate. Focus on the kernel's networking stack.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>

    <action type="reasoning" id="security_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Security" section for the Linux kernel guide. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. The length should be around 1500-2000 words. Reference source code locations when appropriate. Focus on kernel security features like SELinux, AppArmor, namespaces, and capabilities.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>

    <action type="reasoning" id="interrupt_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Interrupt Handling" section for the Linux kernel guide. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. The length should be around 1500-2000 words. Reference source code locations when appropriate. Explain the use of Interrupts and exception handling in the linux kernel. What happens when the processor encounters an interrupt. What IRQ lines can be used for.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>

    <action type="reasoning" id="locking_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Locking Mechanisms" section for the Linux kernel guide. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. The length should be around 1500-2000 words. Reference source code locations when appropriate. Explain Mutex, Semaphores, Spinlocks.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>

    <action type="reasoning" id="tracing_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Tracing and Debugging" section for the Linux kernel guide. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. The length should be around 1500-2000 words. Reference source code locations when appropriate. Explain ftrace, perf and other methods available to debug problems within the Linux kernel.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>

    <action type="reasoning" id="resources_content" model="anthropic/claude-3.5-sonnet" depends_on="kernel_guide_outline">
        <content>
You are an expert on the Linux kernel. Based on the following outline, write the "Resources and Further Reading" section for the Linux kernel guide. This guide assumes the reader is familiar with Linux concepts, but wants a deeper technical level of understanding. The length should be around 500-750 words. Provide links to the source code repository, documentation, and related projects. Explain how to access different revision controlled branches within the linux kernel. For what reasons would the use of those branches be for.

Outline:
{{outputs.kernel_guide_outline.raw_response}}
        </content>
    </action>
    
    <action type="python" id="assemble_guide" depends_on="kernel_guide_outline,introduction_content,process_management_content,memory_management_content,virtual_filesystem_content,device_drivers_content,networking_content,security_content,interrupt_content,locking_content,tracing_content,resources_content">
        <content>
import json
import markdown
import datetime

try:
    guide_outline = json.loads(outputs["kernel_guide_outline"]["raw_response"])
    introduction = outputs["introduction_content"]["raw_response"]
    process_management = outputs["process_management_content"]["raw_response"]
    memory_management = outputs["memory_management_content"]["raw_response"]
    vfs = outputs["virtual_filesystem_content"]["raw_response"]
    device_drivers = outputs["device_drivers_content"]["raw_response"]
    networking = outputs["networking_content"]["raw_response"]
    security = outputs["security_content"]["raw_response"]
    interrupt = outputs["interrupt_content"]["raw_response"]
    locking = outputs["locking_content"]["raw_response"]
    tracing = outputs["tracing_content"]["raw_response"]
    resources = outputs["resources_content"]["raw_response"]

    guide_title = guide_outline["title"]

    markdown_content = f"# {guide_title}\n\n" + introduction + "\n\n" + process_management + "\n\n" + memory_management + "\n\n" + vfs + "\n\n" + device_drivers + "\n\n" + networking + "\n\n" + security + "\n\n" + interrupt + "\n\n" + locking + "\n\n" + tracing + "\n\n" + resources
    
    timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    filename = f"linux_kernel_guide_{timestamp}.md"
    
    with open(filename, "w") as f:
        f.write(markdown_content)

    print(f"Linux kernel guide assembled into {filename}")

except Exception as e:
    print(f"Error assembling guide: {e}")
</content>
    </action>
</actions>"""},
    {"role": "user", "content": "Generate an XML action plan to: Go get today's news and summarize it in a document"},
    {"role": "assistant", "content": """<?xml version="1.0"?>
<actions>
    <action type="python" id="fetch_google_news">
        <content>
import requests
from bs4 import BeautifulSoup

try:
    response = requests.get("https://news.google.com/news/rss")
    response.raise_for_status()

    soup = BeautifulSoup(response.content, "xml")
    items = soup.find_all("item")
    news_items = []
    for item in items:
        title = item.find("title").text
        link = item.find("link").text
        description = item.find("description").text
        news_items.append({"title": title, "link": link, "description": description})

    print(news_items)
except requests.exceptions.RequestException as e:
    print(f"Error fetching Google News: {e}")
    print("[]")
except Exception as e:
    print(f"Error parsing Google News: {e}")
    print("[]")
</content>
    </action>

    <action type="python" id="fetch_wikipedia">
        <content>
import requests
from bs4 import BeautifulSoup

try:
    response = requests.get("https://en.wikipedia.org/wiki/Portal:Current_events")
    response.raise_for_status()
    soup = BeautifulSoup(response.content, "html.parser")
    
    # Extract relevant sections like "From today's featured article", "Did you know...", "In the news"
    featured_article = soup.find(id="mp-tfa")
    did_you_know = soup.find(id="mp-dyk")
    in_the_news = soup.find(id="mp-itn")

    wikipedia_content = {}

    if featured_article:
        wikipedia_content["featured_article"] = featured_article.text
    if did_you_know:
        wikipedia_content["did_you_know"] = did_you_know.text
    if in_the_news:
        wikipedia_content["in_the_news"] = in_the_news.text

    print(wikipedia_content)

except requests.exceptions.RequestException as e:
    print(f"Error fetching Wikipedia: {e}")
    print("{}")
except Exception as e:
    print(f"Error parsing Wikipedia: {e}")
    print("{}")
</content>
    </action>

    <action type="reasoning" id="compile_summary" model="google/gemini-2.0-flash-001" depends_on="fetch_google_news,fetch_wikipedia">
        <content>
Compile a summary of the news of the day based on the following information:

Google News: {{outputs.fetch_google_news.raw_response}}

Wikipedia Main Page: {{outputs.fetch_wikipedia.raw_response}}

Make sure to include links. Aim for a 5 minute read. Only return the document please.

</content>
    </action>

    <action type="python" id="save_summary" depends_on="compile_summary">
        <content>
import datetime

try:
    summary = outputs["compile_summary"]["raw_response"]
    timestamp = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
    filename = f"news_summary_{timestamp}.md"

    with open(filename, "w") as f:
        f.write(f"# News Summary - {timestamp}\\n\\n{summary}")

    print(f"Summary saved to {filename}")
except Exception as e:
    print(f"Error saving summary: {e}")
</content>
    </action>
</actions>"""}
]

JSON_SYSTEM_PROMPT = """You MUST return valid JSON:
- Be CONCISE - trim all unnecessary fields/variables                                                                                                                                                                                               
- Summarize lengthy content instead of verbatim inclusion                                                                                                                                                                                          
- Use short property names where possible                                                                                                                                                                                                          
- If content exceeds 200 characters, provide a summary    
- Escape special characters
- No markdown code blocks
"""

CONTENT_SYSTEM_PROMPT = """You are a valuable part of a content production pipeline. Please produce the content specified with ZERO editorialization. Given any specifications (style, length, formatting) you must match them exactly. If asked to stitch together and format parts, do not leave out a single sentence from the original."""
