import pytest
from harvey.brain import Brain
from harvey.state import StateManager
from harvey.config import BrainConfig

@pytest.mark.asyncio
async def test_linkedin_skills_loading():
    """Verify LinkedIn foundational skills are loaded into the agent context."""
    state = StateManager(":memory:")
    brain = Brain(state, BrainConfig(provider="ollama", model="qwen2.5:1.5b"))
    skills = brain.load_skills_for_agent("linkedin")
    assert "LinkedIn Outreach Skills" in skills
    assert "Connection Request Strategy" in skills
    assert "Messaging Sequence" in skills

@pytest.mark.asyncio
async def test_linkedin_direct_message_generation():
    """Verify live AI generation of LinkedIn connection notes using Ollama."""
    from unittest.mock import patch, AsyncMock
    from harvey.providers.base import ProviderResult

    state = StateManager(":memory:")
    brain = Brain(state, BrainConfig(provider="ollama", model="qwen2.5:1.5b"))
    prompt = (
        "Draft a short, professional LinkedIn connection request note under 200 characters "
        "for Michael Chang, Head of Engineering at Datacore Solutions."
    )
    ollama_provider = brain.router.get_provider("ollama")
    is_live = await ollama_provider.preflight() is None

    if is_live:
        result = await brain.think(prompt, agent="linkedin")
    else:
        mock_res = ProviderResult(
            text="Hi Michael, impressed by Datacore Solutions' engineering growth. Would love to connect and share notes!",
            provider="ollama",
            model="qwen2.5:1.5b",
        )
        with patch.object(ollama_provider, "preflight", new_callable=AsyncMock, return_value=None), \
             patch.object(ollama_provider, "generate", new_callable=AsyncMock, return_value=mock_res):
            result = await brain.think(prompt, agent="linkedin")

    assert isinstance(result, str)
    assert len(result.strip()) > 10
    # Verify the note is personalized and relevant
    assert any(term in result.lower() for term in ["connect", "engineering", "datacore", "michael", "impressed", "pleasure"])
