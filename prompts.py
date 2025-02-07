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
  * Reasoning: {{outputs.ID.raw_response}}"""

PLANNING_EXAMPLES = [
    {
        "user": "Generate an XML action plan to: find the current president",
        "assistant": """<?xml version="1.0"?>
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
</actions>"""
    },
    {
        "user": "Create a technical document outline with analysis",
        "assistant": """<?xml version="1.0"?>
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
</actions>"""
    },
    {
        "user": "go to google news and summarize it for me",
        "assistant": """<?xml version="1.0"?>
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
</actions>"""
    }
]
