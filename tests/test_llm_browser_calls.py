
import sys
import logging
from winston.main import Winston
from winston.core.brain import Brain

# Configure logging
logging.basicConfig(level=logging.INFO, format='%(message)s')

def test_llm_skill_generation():
    print("Testing what skill calls the Brain generates for the MacBook configuration prompt...")
    
    # Initialize components
    winston = Winston()
    brain = winston.brain
    
    # The exact prompt the user used
    prompt = "Gehe auf apple.com, konfiguriere das teuerste MacBook Pro mit maximalem Arbeitsspeicher und Speicherplatz, und zeig mir dann den Screenshot vom Warenkorb."
    
    # Ask the brain to think
    print(f"\nUser Prompt: {prompt}\n")
    print("Waiting for LLM response...")
    
    response = brain.think(prompt)
    
    print("\n--- LLM RAW RESPONSE ---")
    print(response)
    print("------------------------\n")
    
    # Parse the skill calls to see what it actually intended to do
    skill_calls = brain.parse_skill_calls(response)
    
    print("--- PARSED SKILL CALLS ---")
    if not skill_calls:
        print("No skill calls detected.")
    else:
        for i, call in enumerate(skill_calls):
            print(f"{i+1}. Skill: {call.get('skill', 'unknown')}")
            print(f"   Params: {call.get('parameters', {})}")
    print("--------------------------\n")

if __name__ == "__main__":
    test_llm_skill_generation()
