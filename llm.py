import os
import re

from openai import OpenAI

BASE_URL = "https://generativelanguage.googleapis.com/v1beta/openai/"
FAST_MODEL = "gemini-2.5-flash"
STRONG_MODEL = "gemini-2.5-pro"

_FENCE = re.compile(r"```(?:python)?\n(.*?)```", re.DOTALL)
_client = None


def _get_client():
    global _client
    if _client is None:
        _client = OpenAI(api_key=os.environ["GEMINI_API_KEY"], base_url=BASE_URL)
    return _client


def call(system, user, model=FAST_MODEL, max_tokens=4096):
    resp = _get_client().chat.completions.create(
        model=model,
        max_tokens=max_tokens,
        messages=[
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ],
    )
    return resp.choices[0].message.content


def fenced_blocks(text):
    return [block.strip() for block in _FENCE.findall(text)]