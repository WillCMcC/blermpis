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
    {"role": "user", "content": "Generate an XML action plan to: find the current president"},
    {"role": "assistant", "content": """<?xml version="1.0"?>
<actions>
  <action type="bash" id="1" depends_on="2">
    <content>pip install wikipedia</content>
  </action>
  <action type="python" id="2">
    <content>
import wikipedia
try:
    president_page = wikipedia.page("President of the United States")
    print(president_page.content)
except wikipedia.exceptions.PageError:
    print("Error: Wikipedia page not found.")
except wikipedia.exceptions.DisambiguationError as e:
    print(f"Error: {e.options}")
    </content>
  </action>
  <action type="reasoning" id="3" model="google/gemini-2.0-flash-001" depends_on="1">
    <content>Based on {{outputs.1.raw_response}}, identify current president.</content>
  </action>
</actions>"""},
    {"role": "user", "content": "Create a technical document outline with analysis"},
    {"role": "assistant", "content": """<?xml version="1.0"?>
<actions>
  <action type="reasoning" id="plan" model="deepseek/deepseek-r1" depends_on="priority" format="json">
    <content>Create outline focused on technical aspects...</content>
  </action>
  <action type="reasoning" id="1" model="anthropic/claude-3.5-sonnet" depends_on="plan">
    <content>Expand {{outputs.plan.raw_response}} into detailed analysis...</content>
  </action>
  <action type="python" id="2" depends_on="plan">
    <content>
try:
    data = outputs["plan"]["response_json"]
    print(f"Processed result: {data['content']}")
except Exception as e:
    print(f"Error processing output: {str(e)}")
    </content>
  </action>
</actions>"""},
    {"role": "user", "content": "go to google news and summarize it for me"},
    {"role": "assistant", "content": """<?xml version="1.0"?>
<actions>
  <action type="python" id="1" model="google/gemini-2.0-flash-001">
    <content>
import requests
from bs4 import BeautifulSoup

try:
    url = "https://news.google.com/news/rss"
    response = requests.get(url)
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
    print(f"Request Error: {e}")
except Exception as e:
    print(f"An error occurred: {e}")
</content>
  </action>
  <action type="reasoning" id="2" model="google/gemini-2.0-flash-001" depends_on="1">
    <content>Summarize the following news articles: {{outputs.1.raw_response}}</content>
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
- Include ALL data fields"""

CONTENT_SYSTEM_PROMPT = """You are a valuable part of a content production pipeline. Please produce the content specified with ZERO editorialization. Given any specifications (style, length, formatting) you must match them exactly. If asked to stitch together and format parts, do not leave out a single sentence from the original. NEVER produce incomplete content -- prioritizing ending neatly before tokens run out."""
