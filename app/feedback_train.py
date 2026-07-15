import os
import json
from typing import Dict, Any, List

FEEDBACK_FILE = os.path.join(os.path.dirname(__file__), "feedback_samples.json")

def save_feedback_sample(sample_data: Dict[str, Any]):
    """
    Append an operator correction feedback sample to a local JSON file.
    """
    samples = []
    if os.path.exists(FEEDBACK_FILE):
        try:
            with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
                samples = json.load(f)
        except Exception as e:
            print(f"Error loading feedback samples: {e}")
            
    # Append the new sample
    samples.append(sample_data)
    
    try:
        with open(FEEDBACK_FILE, "w", encoding="utf-8") as f:
            json.dump(samples, f, ensure_ascii=False, indent=4)
        print(f"Successfully saved human match feedback sample. Total samples: {len(samples)}")
    except Exception as e:
        print(f"Error saving feedback sample: {e}")

def get_feedback_suggestions() -> List[Dict[str, Any]]:
    """
    Analyze collected feedback samples to generate alias/typo correction suggestions.
    For example, if raw_text has a term 'X' and user consistently maps it to material 'Y',
    we suggest adding 'X' -> 'Y' alias.
    """
    if not os.path.exists(FEEDBACK_FILE):
        return []
        
    try:
        with open(FEEDBACK_FILE, "r", encoding="utf-8") as f:
            samples = json.load(f)
    except Exception as e:
        print(f"Error reading feedback samples for analysis: {e}")
        return []
        
    # Simple recommendation algorithm:
    # Identify items where manual_code != auto_code and is_correct is False
    suggestions = []
    mismatch_counts = {}
    
    for s in samples:
        if not s.get("is_correct") and s.get("manual_code") and s.get("auto_code") != s.get("manual_code"):
            raw_text = s.get("raw_text", "")
            # Try to identify word differences
            # If the user matched raw_text containing some typo to the manual_code,
            # we query manual_code to see standard name, and check if there's a typo.
            manual_code = s.get("manual_code")
            from app.db_manager import query_by_code
            material = query_by_code(manual_code)
            if material:
                std_name = material.get("name", "")
                # If std_name is different from words in raw_text, compile a suggestion
                key = (raw_text, std_name)
                mismatch_counts[key] = mismatch_counts.get(key, 0) + 1

    # Keep suggestions that appear at least once
    for (raw, std), count in mismatch_counts.items():
        # Clean up strings
        raw_clean = raw.strip()
        std_clean = std.strip()
        if raw_clean and std_clean and raw_clean != std_clean:
            suggestions.append({
                "suggested_typo": raw_clean[:20], # truncate if too long
                "suggested_correction": std_clean,
                "confidence_count": count
            })
            
    # Sort suggestions by confidence count descending
    suggestions.sort(key=lambda x: x["confidence_count"], reverse=True)
    return suggestions
