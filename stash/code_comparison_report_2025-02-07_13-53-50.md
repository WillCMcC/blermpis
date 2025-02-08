
# Code Comparison Report

## Summary of Changes

The diff represents substantial changes to the `Agent` class and the `AgentCLI` class, introducing improvements in error handling, state management, and execution flow, along with more robust logging and user feedback mechanisms.

Here's a summary of the key differences:

**General Improvements:**

*   **Logging:** Enhanced logging using the `logging` module instead of `print` statements. Configured logging to include timestamps and level information.
*   **Error Handling:** Improved error handling for XML parsing, API calls, and job executions with more detailed error messages and contextual information, including job IDs, types, and dependencies
*   **API Keys:** Defined dedicated variables for DeepSeek, OpenRouter, and Groq API keys along with their respective base URLs.
*   **Subjob Handling:** Introduced a UUID to reasoning subjobs (model calls from Python scripts) to avoid ID collisions and added retry logic.
*   **Timeout:** Added timeout to reasoning jobs to prevent hanging.
*   **Dependency Handling:** Enhanced tracking and reporting of missing dependencies.

**Agent Class Changes:**

*   **Job Data Class:** Introduces a `Job` dataclass to organize information about each job, with fields for `id`, `type`, `content`, `depends_on`, `status`, `output_ref`, `model` and `response_format`.
*   **Model and Response Format at Job Level:** Added `model` and `response_format` attributes to the `Job` class, limiting the use of custom models and formats to reasoning jobs only and issuing a warning when these attributes are specified for non-reasoning jobs.
*   **XML Content Extraction:** Improved the robustness of XML content extraction when parsing `action_xml` and implemented more comprehensive validation.
*   **Job Queue Processing:** Significantly changed the queue processing logic including adding bash command timeout and error handling

**AgentCLI Class Changes:**

*   **Feedback History:** Implemented a feedback history to retain user feedback across multiple regenerations. Includes a maximum size limit.
*   **Plan Regeneration:** Enhanced plan regeneration by including feedback and the previous plan in the prompt.
*   **Post-Execution Options:** Extensive post-execution options to re-run queries/plans, add feedback, show XML, and save current jobs.
*   **XML Validation:** Added better XML validation within the CLI with line numbers
*   **Input Requests:** Added XML support for `<request_input>`
*   **Saved Jobs:** Implemented `_save_job` and `_load_job` for saving and loading jobs in the jobs directory.
*   **API Key Check:** Added an API key check before processing jobs.
*    **Response Handling:** Modified the logic to handle responses and implemented `_process_and_handle_response` function to consolidate the processing.
*   **Clean State:** Clear the state to prevent errors when completing.

