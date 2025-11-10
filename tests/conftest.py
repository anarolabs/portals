"""Pytest configuration and fixtures."""

import pytest


@pytest.fixture
def sample_document_content() -> str:
    """Sample markdown content for testing."""
    return """# Test Document

This is a test document for Portals.

## Features

- Bullet point 1
- Bullet point 2

## Code Example

```python
def hello_world():
    print("Hello, Portals!")
```

That's all!
"""


@pytest.fixture
def sample_document_title() -> str:
    """Sample document title."""
    return "Test Document"
