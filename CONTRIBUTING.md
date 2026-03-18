# Contributing to W.I.N.S.T.O.N.

Thanks for your interest in contributing! Winston is an open-source project and we welcome contributions of all kinds.

## Ways to Contribute

- **Add a skill** — The easiest way to extend Winston
- **Add a channel** — Slack, Matrix, Signal, SMS, etc.
- **Improve existing skills** — Better error handling, more features
- **Write tests** — We have 408+ tests, more is always better
- **Fix bugs** — Check the Issues tab
- **Improve docs** — Tutorials, guides, translations

## Development Setup

```bash
# Clone
git clone https://github.com/serhatbilge/W.I.N.S.T.O.N..S.git
cd W.I.N.S.T.O.N..S

# Create virtual environment
python3 -m venv venv
source venv/bin/activate

# Install dependencies
pip install -r requirements.txt

# Run tests
python -m pytest tests/ -q

# Start in dev mode
python -m winston.main --mode text --debug
```

## Adding a New Skill

1. Create `winston/skills/your_skill.py`:

```python
from winston.skills.base import BaseSkill, SkillResult

class YourSkill(BaseSkill):
    name = "your_skill"
    description = "What does it do — the LLM reads this"
    parameters = {
        "query": {"type": "string", "description": "The input"},
    }

    def execute(self, query: str = "", **kwargs) -> SkillResult:
        result = f"Hello from your skill: {query}"
        return SkillResult(success=True, message=result)
```

2. Register in `winston/main.py` → `_register_skills()`:
```python
from winston.skills.your_skill import YourSkill
skills.append(YourSkill())
```

3. Add a test in `tests/test_your_skill.py`

4. Run `python -m pytest tests/ -q` to verify everything passes

## Code Style

- Python 3.9+ compatible
- Type hints where they help readability
- Docstrings on classes and public methods
- Use `logging` (not `print`) for diagnostic output
- Every skill must return `SkillResult`
- Skills should handle their own errors gracefully

## Testing

```bash
# Run all unit tests
python -m pytest tests/ -q

# Run a specific test file
python -m pytest tests/test_scheduler.py -v

# Run with coverage
python -m pytest tests/ --cov=winston --cov-report=term-missing
```

## Pull Request Process

1. Fork the repo and create a feature branch (`git checkout -b feature/my-skill`)
2. Write your code + tests
3. Make sure all tests pass: `python -m pytest tests/ -q`
4. Keep commits focused — one feature/fix per PR
5. Open a PR with a clear description of what you added/changed

## Security

- Never commit API keys or secrets
- All user input must be validated at system boundaries
- External content (web pages, search results) must be wrapped via `security/content_wrapper.py`
- File operations must respect protected directory lists
- Network requests must pass SSRF validation (`security/ssrf_guard.py`)

## License

By contributing, you agree that your contributions will be licensed under the project's AGPL-3.0 license.
