import pytest
import pytest_asyncio
from unittest.mock import AsyncMock, MagicMock
from app.joyreactor.client import JoyReactorClient
from app.joyreactor.models import JRPost, JRTag
from httpx import Response, Request

@pytest.fixture
def mock_client():
    client = JoyReactorClient()
    client.client = AsyncMock()
    return client

@pytest.mark.asyncio
async def test_execute_graphql_success(mock_client):
    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"data": {"test": "value"}}
    mock_client.client.post.return_value = mock_response

    result = await mock_client.execute("query { test }")
    assert result == {"test": "value"}

@pytest.mark.asyncio
async def test_execute_graphql_errors(mock_client):
    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {"errors": [{"message": "GraphQL error"}]}
    mock_client.client.post.return_value = mock_response

    with pytest.raises(Exception) as excinfo:
        await mock_client.execute("query { test }")
    assert "GraphQL errors" in str(excinfo.value)

@pytest.mark.asyncio
async def test_execute_http_403(mock_client):
    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 403
    # Mock raise_for_status to actually raise
    import httpx
    mock_response.raise_for_status.side_effect = httpx.HTTPStatusError("403 Forbidden", request=Request("POST", "url"), response=mock_response)
    mock_client.client.post.return_value = mock_response

    with pytest.raises(httpx.HTTPStatusError):
        await mock_client.execute("query { test }")

@pytest.mark.asyncio
async def test_normalize_media_type(mock_client):
    assert mock_client._normalize_media_type("image/jpeg") == "image"
    assert mock_client._normalize_media_type("video/mp4") == "video"
    assert mock_client._normalize_media_type("unknown/type") == "image"

@pytest.mark.asyncio
async def test_search_tags_parsing(mock_client):
    mock_response = MagicMock(spec=Response)
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "data": {
            "searchTags": [
                {"id": "1", "name": "test_tag", "count": 100, "nsfw": False, "unsafe": False}
            ]
        }
    }
    mock_client.client.post.return_value = mock_response

    tags = await mock_client.search_tags("test")
    assert len(tags) == 1
    assert tags[0].name == "test_tag"
    assert tags[0].id == "1"
