import pytest
from unittest.mock import AsyncMock, MagicMock, patch
from jobsgrep.sources.workday import parse_workday_date, WorkdaySource
from jobsgrep.models import ParsedQuery

def test_parse_workday_date():
    # Relative dates
    assert parse_workday_date("Posted Today") != ""
    assert parse_workday_date("Posted Yesterday") != ""
    assert parse_workday_date("Posted 3 Days Ago") != ""
    assert parse_workday_date("Posted 30+ Days Ago") == ""
    assert parse_workday_date("") == ""

@pytest.mark.asyncio
async def test_workday_source_fetch():
    source = WorkdaySource()
    
    mock_response = MagicMock()
    mock_response.status_code = 200
    mock_response.json.return_value = {
        "jobPostings": [
            {
                "title": "Senior GPU Architect",
                "externalPath": "/job/Santa-Clara/Senior-GPU-Architect_JR123",
                "locationsText": "Santa Clara, CA",
                "postedOn": "Posted Today"
            }
        ]
    }
    
    # We patch _post directly to return our mock response
    with patch.object(source, "_post", new_callable=AsyncMock) as mock_post:
        mock_post.return_value = mock_response
        
        query = ParsedQuery()
        jobs = await source.fetch_jobs(query)
        
        # We check that we received jobs for each target company
        from jobsgrep.sources.workday import WORKDAY_TARGETS
        assert len(jobs) == len(WORKDAY_TARGETS)
        assert jobs[0].title == "Senior GPU Architect"
        assert jobs[0].company == "Nvidia"
        assert jobs[0].location == "Santa Clara, CA"
        assert jobs[0].url == "https://nvidia.wd5.myworkdayjobs.com/en-US/NVIDIAExternalCareerSite/job/Santa-Clara/Senior-GPU-Architect_JR123"
        assert jobs[0].source == "workday"
