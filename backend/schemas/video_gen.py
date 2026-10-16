from typing import List, Optional

from pydantic import BaseModel, Field


class PromptFromVideoRequest(BaseModel):
    """Extract a reusable text-to-video prompt from a source video."""

    url: str = Field(..., description="YouTube URL, Google Drive link, or /api/video/<id> for an upload")
    api_key: Optional[str] = Field(None, description="Gemini or Moonshot/Kimi API key")
    model: Optional[str] = Field("", description="Preferred AI model for prompt extraction")
    style_hint: Optional[str] = Field(None, description="Optional visual style to steer the prompt")
    extra_instructions: Optional[str] = Field(None, description="Optional extra direction for the prompt")
    proxy: Optional[str] = Field(None, description="Optional custom proxy URL")
    cookies: Optional[str] = Field(None, description="Optional YouTube cookies from browser LocalStorage")
    subtitles: Optional[str] = Field(None, description="Optional manual subtitles (SRT or TXT)")
    subtitles_filename: Optional[str] = Field(None, description="Optional manual subtitles filename")


class PromptFromVideoBatchRequest(BaseModel):
    """Extract prompts from several source videos in one background batch."""

    urls: List[str] = Field(
        ...,
        min_length=1,
        max_length=20,
        description="Source videos; each entry follows the `url` rules (max 20 per batch)",
    )
    api_key: Optional[str] = Field(None, description="Gemini or Moonshot/Kimi API key")
    model: Optional[str] = Field("", description="Preferred AI model for prompt extraction")
    style_hint: Optional[str] = Field(None, description="Visual style applied to every item")
    extra_instructions: Optional[str] = Field(None, description="Extra direction applied to every item")
    proxy: Optional[str] = Field(None, description="Optional custom proxy URL")
    cookies: Optional[str] = Field(None, description="Optional YouTube cookies from browser LocalStorage")
    subtitles: Optional[str] = Field(None, description="Optional manual subtitles (SRT or TXT)")
    subtitles_filename: Optional[str] = Field(None, description="Optional manual subtitles filename")


class ShotListItem(BaseModel):
    time: str = Field("", description="Time range, e.g. '0-5s'")
    description: str = Field("", description="What the camera shows in this shot")


class PromptBatchItem(BaseModel):
    """Per-video state inside a prompt extraction batch."""

    url: str
    status: str = Field("pending", description="'pending', 'processing', 'completed' or 'error'")
    video_id: Optional[str] = None
    title: Optional[str] = None
    model: Optional[str] = None
    prompt: Optional[str] = None
    summary: Optional[str] = None
    style: Optional[str] = None
    camera: Optional[str] = None
    lighting: Optional[str] = None
    audio: Optional[str] = None
    setting: Optional[str] = None
    negative_prompt: Optional[str] = None
    shot_list: List[ShotListItem] = Field(default_factory=list)
    error: Optional[str] = None


class PromptBatchStatusResponse(BaseModel):
    batch_id: str
    status: str = Field("running", description="'running', 'completed' or 'error'")
    total: int = 0
    completed: int = 0
    failed: int = 0
    skipped: List[str] = Field(default_factory=list, description="Duplicate URLs dropped on submit")
    items: List[PromptBatchItem] = Field(default_factory=list)


class PromptFromVideoResponse(BaseModel):
    video_id: str
    title: Optional[str] = None
    channel: Optional[str] = None
    duration: Optional[float] = None
    model: Optional[str] = None

    prompt: str
    summary: Optional[str] = None
    style: Optional[str] = None
    subject: Optional[str] = None
    action: Optional[str] = None
    setting: Optional[str] = None
    camera: Optional[str] = None
    lighting: Optional[str] = None
    audio: Optional[str] = None
    negative_prompt: Optional[str] = None
    shot_list: List[ShotListItem] = Field(default_factory=list)

    transcript: Optional[str] = None


class GenerateVideoRequest(BaseModel):
    """Generate a video from a text prompt with Google Veo."""

    prompt: str = Field(..., description="The text-to-video prompt")
    api_key: Optional[str] = Field(None, description="Gemini API key (Veo requires Google Gemini)")
    model: Optional[str] = Field(None, description="Veo model id; defaults to the newest available")
    aspect_ratio: Optional[str] = Field("16:9", description="'16:9' landscape or '9:16' portrait")
    resolution: Optional[str] = Field("720p", description="'720p', '1080p' or '4k'")
    duration_seconds: Optional[int] = Field(8, description="Clip length: 4, 6 or 8 seconds")
    number_of_videos: Optional[int] = Field(1, description="How many variants to generate (1-4)")
    negative_prompt: Optional[str] = Field(None, description="Content to avoid in the generated video")


class GeneratedVideoFile(BaseModel):
    filename: str
    download_url: str


class VideoGenerationStatusResponse(BaseModel):
    job_id: str
    status: str
    stage: Optional[str] = None
    progress: Optional[int] = None
    elapsed: Optional[int] = None
    model: Optional[str] = None
    videos: List[GeneratedVideoFile] = Field(default_factory=list)
    error: Optional[str] = None
