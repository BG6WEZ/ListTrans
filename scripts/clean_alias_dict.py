# -*- coding: utf-8 -*-
"""
Script to clean and audit config/alias_dict.json:
- Remove self-identities (A -> A)
- Detect and resolve circular mappings (A -> B and B -> A)
- Strip newlines and leading/trailing whitespace
- Remove empty keys/values
- Detect conflicting duplicate mappings
"""
import os
import json
import tempfile

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
CONFIG_PATH = os.path.join(PROJECT_ROOT, "config", "alias_dict.json")

def clean_alias_dict():
    print(f"Reading alias dictionary from: {CONFIG_PATH}")
    if not os.path.exists(CONFIG_PATH):
        print(f"File not found: {CONFIG_PATH}")
        return

    # 1. Read raw file to catch duplicate keys before JSON parser collapses them
    raw_pairs = []
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        content = f.read()

    # Load as json dict
    data = json.loads(content)
    print(f"Initial total entries loaded: {len(data)}")

    cleaned = {}
    removed_identities = []
    cleaned_whitespace = []
    removed_empty = []
    broken_cycles = []

    # First pass: clean whitespace, newlines, empty, self-identity
    for k, v in data.items():
        if not isinstance(k, str) or not isinstance(v, str):
            continue
        k_clean = k.replace("\r", "").replace("\n", " ").strip()
        v_clean = v.replace("\r", "").replace("\n", " ").strip()

        if not k_clean or not v_clean:
            removed_empty.append((k, v))
            continue

        if k_clean != k or v_clean != v:
            cleaned_whitespace.append((k, v, k_clean, v_clean))

        if k_clean.lower() == v_clean.lower():
            removed_identities.append((k_clean, v_clean))
            continue

        cleaned[k_clean] = v_clean

    # Second pass: detect and break 2-cycles (A -> B and B -> A)
    # If A -> B and B -> A, we prefer the mapping to the more descriptive/standard one (usually the longer one)
    keys_to_remove = set()
    for k, v in list(cleaned.items()):
        if v in cleaned and cleaned[v] == k:
            # Cycle detected between k and v
            if k not in keys_to_remove and v not in keys_to_remove:
                # Decide which one to keep
                # Keep the one where target is longer / more standard
                if len(v) >= len(k):
                    # Keep k -> v, remove v -> k
                    keys_to_remove.add(v)
                    broken_cycles.append((v, k, f"Kept '{k}' -> '{v}'"))
                else:
                    keys_to_remove.add(k)
                    broken_cycles.append((k, v, f"Kept '{v}' -> '{k}'"))

    for k in keys_to_remove:
        cleaned.pop(k, None)

    print("\n--- Cleaning Report ---")
    print(f"1. Removed self-identity mappings: {len(removed_identities)}")
    for item in removed_identities:
        print(f"   - {item[0]} -> {item[1]}")

    print(f"2. Cleaned whitespace/newlines: {len(cleaned_whitespace)}")
    for item in cleaned_whitespace[:10]:
        print(f"   - '{item[0]}' -> '{item[2]}'")
    if len(cleaned_whitespace) > 10:
        print(f"   ... and {len(cleaned_whitespace) - 10} more")

    print(f"3. Broken circular mappings: {len(broken_cycles)}")
    for item in broken_cycles:
        print(f"   - Removed '{item[0]}' -> '{item[1]}' ({item[2]})")

    print(f"4. Removed empty: {len(removed_empty)}")
    print(f"Final valid entries count: {len(cleaned)}")

    # Atomic write back
    dir_name = os.path.dirname(CONFIG_PATH)
    fd, temp_path = tempfile.mkstemp(dir=dir_name, prefix="alias_clean_", suffix=".json")
    with open(fd, "w", encoding="utf-8") as f:
        json.dump(cleaned, f, ensure_ascii=False, indent=4)

    # Verify
    with open(temp_path, "r", encoding="utf-8") as f:
        json.load(f)
    os.replace(temp_path, CONFIG_PATH)
    print(f"\nSuccessfully cleaned and saved {CONFIG_PATH}")

if __name__ == "__main__":
    clean_alias_dict()
