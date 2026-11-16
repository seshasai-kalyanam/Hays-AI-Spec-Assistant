import json
import os
import sys
from datetime import datetime
from pathlib import Path

# Add parent directory to path for imports
ROOT_DIR = Path(__file__).resolve().parent.parent
if str(ROOT_DIR) not in sys.path:
    sys.path.insert(0, str(ROOT_DIR))

from qa.llm_client import GENERATION_CONFIG, configure_client, get_gemini_client  # type: ignore
from utils.gemini_client import generation_config_from_dict  # type: ignore
from utils.gemini_prompts import build_pdf_chat_system_prompt

def read_json_file(filepath):
    """Read and return the content of a JSON file."""
    try:
        with open(filepath, 'r', encoding='utf-8') as file:
            return json.load(file)
    except UnicodeDecodeError:
        # If UTF-8 fails, try with UTF-8-sig (BOM handling)
        with open(filepath, 'r', encoding='utf-8-sig') as file:
            return json.load(file)

def format_number(value):
    """Format a number to 2 decimal places if it's a float, otherwise return as is."""
    if value is None:
        return None
    if isinstance(value, (int, float)):
        if isinstance(value, float):
            return round(value, 2)
        return value
    return value


def _generate_ai_summary(qa_pairs, qa_evaluation):
    """Generate AI-powered analysis of best and worst responses using the same model as evaluation."""
    try:
        configure_client()
        client = get_gemini_client()

        # Extract evaluation results with scores
        results = qa_evaluation.get("results", [])
        if not results:
            return "AI analysis unavailable: No evaluation results found."

        # Sort by score to get best and worst
        scored_results = []
        for i, result in enumerate(results):
            score = result.get("score", 0)
            prompt = result.get("prompt", "")
            qa_pair = qa_pairs[i] if i < len(qa_pairs) else {}
            response = qa_pair.get("response", "")
            scored_results.append((score, prompt, response))

        scored_results.sort(key=lambda x: x[0], reverse=True)

        # Get top 5 and bottom 5 (or all if less than 10)
        n_items = min(5, len(scored_results))
        best_responses = scored_results[:n_items]
        worst_responses = scored_results[-n_items:] if len(scored_results) >= n_items else []

        # Build prompt for AI analysis
        analysis_prompt = "Analyze the following QA pairs for an electrical construction specifications assistant.\n\n"

        analysis_prompt += "BEST RESPONSES (highest scores):\n"
        for i, (score, prompt, response) in enumerate(best_responses, 1):
            analysis_prompt += f"{i}. Score: {score}\n   Prompt: {prompt}\n   Response: {response}\n\n"

        analysis_prompt += "WORST RESPONSES (lowest scores):\n"
        for i, (score, prompt, response) in enumerate(worst_responses, 1):
            analysis_prompt += f"{i}. Score: {score}\n   Prompt: {prompt}\n   Response: {response}\n\n"

        analysis_prompt += """Please provide a concise analysis (under 300 words) covering:
1. What patterns make the best responses effective?
2. What common issues cause the worst responses to score poorly?
3. Specific actionable insights for improving the system prompt.

Focus on electrical construction terminology, citation quality, response structure, and adherence to specifications."""

        response = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=analysis_prompt,
            config=generation_config_from_dict(GENERATION_CONFIG),
        )
        return response.text if response and hasattr(response, "text") else "AI analysis unavailable: No response from model."

    except Exception as e:
        return f"AI analysis unavailable: {str(e)}"


def _generate_prompt_improvements(ai_summary, current_system_prompt):
    """Generate suggestions for improving the system prompt based on AI analysis."""
    try:
        configure_client()
        client = get_gemini_client()

        improvement_prompt = f"""Based on this analysis of QA pair performance:

{ai_summary}

And this current system prompt:

{current_system_prompt}

Please suggest specific, actionable improvements to the system prompt for an electrical construction specifications assistant. Focus on:
1. Clarifying response structure requirements
2. Strengthening citation guidelines
3. Improving handling of edge cases
4. Making instructions more specific to avoid common mistakes

Provide concrete suggestions with before/after examples where helpful. Keep under 250 words."""

        response = client.models.generate_content(
            model="gemini-flash-lite-latest",
            contents=improvement_prompt,
            config=generation_config_from_dict(GENERATION_CONFIG),
        )
        return response.text if response and hasattr(response, "text") else "System prompt improvement suggestions unavailable: No response from model."

    except Exception as e:
        return f"System prompt improvement suggestions unavailable: {str(e)}"


def generate_markdown_report(qa_pairs, qa_evaluation):
    """Generate a comprehensive Markdown report from the JSON data."""
    report = "# Specification PDF QA Report\n\n"
    report += f"Generated on: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}\n\n"

    # Surface the models used for generation and evaluation so readers know
    # exactly which LLMs produced each artifact.
    qa_model = qa_pairs[0].get("model") if qa_pairs else "N/A"
    eval_model = (
        qa_evaluation.get("evaluation_model")
        or (qa_evaluation.get("results", [{}])[0].get("evaluation_model") if qa_evaluation.get("results") else None)
        or "N/A"
    )

    # Section for Summary (moved to top)
    report += "## Summary\n\n"
    summary = qa_evaluation.get('summary', {})
    response_quality = summary.get('response_quality', summary)

    report += f"**QA Generation Model:** {qa_model}\n\n"
    report += f"**Evaluation Model:** {eval_model}\n\n"

    report += f"**Average Score:** {format_number(response_quality.get('average_score'))} - The mean score across all evaluated QA pairs.\n\n"
    report += f"**Count:** {format_number(response_quality.get('count'))} - Total number of QA pairs evaluated.\n\n"
    report += f"**Median:** {format_number(response_quality.get('median'))} - The middle value when scores are sorted.\n\n"
    report += f"**25th Percentile:** {format_number(response_quality.get('p25'))} - 25% of scores are below this value.\n\n"
    report += f"**75th Percentile:** {format_number(response_quality.get('p75'))} - 75% of scores are below this value.\n\n"
    report += f"**Min Score:** {format_number(response_quality.get('min_score'))} - The lowest score among all evaluations.\n\n"
    report += f"**Max Score:** {format_number(response_quality.get('max_score'))} - The highest score among all evaluations.\n\n"
    report += f"**Score Range:** {format_number(response_quality.get('score_range'))} - The difference between the max and min scores.\n\n"

    if 'high_scores_count' in response_quality:
        report += f"**High Scores (>=0.8):** {format_number(response_quality.get('high_scores_count'))}\n\n"
    if 'medium_scores_count' in response_quality:
        report += f"**Medium Scores (0.6-0.8):** {format_number(response_quality.get('medium_scores_count'))}\n\n"
    if 'low_scores_count' in response_quality:
        report += f"**Low Scores (<0.6):** {format_number(response_quality.get('low_scores_count'))}\n\n"

    report += f"**Total Evaluation Time:** {format_number(summary.get('total_evaluation_time'))} - Total time spent on all evaluations (in seconds).\n\n"
    report += f"**Average Time per Item:** {format_number(summary.get('average_time_per_item'))} - Average time spent per QA pair evaluation (in seconds).\n\n"

    # Generate AI analysis sections
    ai_summary = _generate_ai_summary(qa_pairs, qa_evaluation)
    current_system_prompt = build_pdf_chat_system_prompt()
    prompt_improvements = _generate_prompt_improvements(ai_summary, current_system_prompt)

    # AI Analysis section
    report += "## AI Analysis of Best and Worst Responses\n\n"
    report += f"{ai_summary}\n\n"

    # System Prompt Improvements section
    report += "## System Prompt Improvement Suggestions\n\n"
    report += f"{prompt_improvements}\n\n"

    # Section for QA Pairs
    report += "## QA Pairs\n\n"
    for i, pair in enumerate(qa_pairs, 1):
        report += f"### QA Pair {i}\n\n"
        report += f"**Prompt:** {pair['prompt']}\n\n"
        report += f"**Response:** {pair['response']}\n\n"
        report += f"**Model:** {pair['model']}\n\n"
        report += f"**Timestamp:** {pair.get('timestamp', 'N/A')}\n\n"
        report += f"**Word Limit:** {pair.get('word_limit', 'N/A')}\n\n"
        report += f"**Context File:** {pair.get('context_file', 'N/A')}\n\n"
        report += f"**Processing Time:** {format_number(pair.get('processing_time_seconds', pair.get('processing_time', 'N/A')))} seconds\n\n"
        report += "---\n\n"

    # Section for QA Evaluation
    report += "## QA Pairs Evaluation\n\n"
    for i, eval_item in enumerate(qa_evaluation['results'], 1):
        report += f"### Evaluation {i}\n\n"
        report += f"**Prompt:** {eval_item['prompt']}\n\n"
        report += f"**Score:** {format_number(eval_item['score'])}\n\n"
        report += f"**Evaluation Model:** {eval_item.get('evaluation_model', 'N/A')}\n\n"
        report += f"**Explanation:** {eval_item['explanation']}\n\n"
        report += "---\n\n"

    return report

def main():
    # File paths relative to the script's directory
    script_dir = os.path.dirname(os.path.abspath(__file__))
    qa_pairs_file = os.path.join(script_dir, 'qa_pairs.json')
    qa_evaluation_file = os.path.join(script_dir, 'qa_pairs_evaluation.json')

    # Read JSON files
    qa_pairs = read_json_file(qa_pairs_file)
    qa_evaluation = read_json_file(qa_evaluation_file)

    # Generate Markdown report
    report = generate_markdown_report(qa_pairs, qa_evaluation)

    # Create filename with timestamp
    timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
    filename = os.path.join(script_dir, f"qa_report_{timestamp}.md")

    # Write to file in the same directory as the script
    with open(filename, 'w', encoding='utf-8') as file:
        file.write(report)

    print(f"Report generated: {filename}")

if __name__ == "__main__":
    main()
