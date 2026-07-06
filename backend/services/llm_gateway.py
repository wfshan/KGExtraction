"""
LLM 网关模块
封装 OpenAI 兼容接口调用，支持智能模型路由、重试与流控
"""
import json
import asyncio
import logging
from typing import Any, Dict, List, Optional
from openai import AsyncOpenAI, APIError, RateLimitError, APITimeoutError

from config import load_config
from services.extraction_logger import log_extraction, log_extraction_chunk, log_extraction_prompt

logger = logging.getLogger(__name__)

# 任务复杂度等级
COMPLEXITY_SIMPLE = "simple"
COMPLEXITY_NORMAL = "normal"
COMPLEXITY_COMPLEX = "complex"


class LLMGateway:
    """大模型网关 - 封装统一调用与模型路由"""

    def __init__(self):
        self._client: Optional[AsyncOpenAI] = None
        self._config = None

    def _get_client(self) -> AsyncOpenAI:
        """延迟初始化 AsyncOpenAI 客户端"""
        import os
        # 清除影响连接的代理环境变量
        for var in ["http_proxy", "https_proxy", "HTTP_PROXY", "HTTPS_PROXY",
                    "all_proxy", "ALL_PROXY"]:
            os.environ.pop(var, None)

        config = load_config()
        if self._client is None or self._config != config:
            self._config = config
            self._client = AsyncOpenAI(
                api_key=config.api_key,
                base_url=config.base_url,
                timeout=300.0,  # 增加超时时间以支持复杂的文档剖析任务
            )
        return self._client

    def _select_model(self, complexity: str) -> str:
        """根据任务复杂度选择模型"""
        config = load_config()
        model_map = {
            COMPLEXITY_SIMPLE: config.model_simple,
            COMPLEXITY_NORMAL: config.model_normal,
            COMPLEXITY_COMPLEX: config.model_complex,
        }
        return model_map.get(complexity, config.model_normal)

    async def chat(
        self,
        messages: List[Dict[str, str]],
        complexity: str = COMPLEXITY_NORMAL,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None,
        max_retries: int = 3,
        print_stream: bool = False,
        stream_log: bool = False,
    ) -> Dict[str, Any]:
        """
        调用大模型聊天接口 (异步)
        """
        client = self._get_client()
        selected_model = model or self._select_model(complexity)

        kwargs = {
            "model": selected_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        if response_format:
            kwargs["response_format"] = response_format
            
        # 记录 LLM 请求与输入内容，便于后端执行记录与前端进度详情流式展示
        print(f"[Trace] 调用 LLM ({selected_model}): 消息数={len(messages)}, Prompt 总长度={sum(len(m['content']) for m in messages)} 字符")
        log_extraction(f"调用 LLM (模型: {selected_model}, 复杂度: {complexity})")
        if stream_log:
            log_extraction_prompt(messages, max_user_chars=1200)

        for attempt in range(max_retries):
            try:
                if print_stream:
                    # 仅在需要实时控制台输出时使用流式，避免常规调用的流式开销
                    kwargs["stream"] = True
                    response = await client.chat.completions.create(**kwargs)
                    content = ""
                    
                    if print_stream:
                        print("\n[LLM 思考中...] ", end="", flush=True)
                    if stream_log:
                        # 在普通日志中先打一个前导标记
                        log_extraction("=== 模型思考过程 ===")
                        log_extraction_chunk("\n")
                        
                    async for chunk in response:
                        if chunk.choices and chunk.choices[0].delta.content:
                            text_chunk = chunk.choices[0].delta.content
                            content += text_chunk
                            if print_stream:
                                print(text_chunk, end="", flush=True)
                            if stream_log:
                                log_extraction_chunk(text_chunk)
                                
                    if print_stream:
                        print("\n")
                    if stream_log:
                        log_extraction_chunk("\n====================\n")
                    
                    usage = {} # 流式返回通常不包含完整的 usage 统计，这里简化处理
                else:
                    completion = await client.chat.completions.create(**kwargs)
                    content = completion.choices[0].message.content or ""
                    usage = {}
                    if hasattr(completion, "usage") and completion.usage:
                        usage = {
                            "prompt_tokens": completion.usage.prompt_tokens,
                            "completion_tokens": completion.usage.completion_tokens,
                            "total_tokens": completion.usage.total_tokens,
                        }
                    if stream_log:
                        # 非流式调用时，保留模型输出日志，避免高频抽取场景的流式开销
                        log_extraction("=== 模型返回 ===")
                        log_extraction_chunk(content)
                        log_extraction_chunk("\n====================\n")
                
                log_extraction(f"LLM 返回成功 ({usage.get('total_tokens', '未知')} tokens)")
                if not print_stream:
                    log_extraction(f"Response (Prefix): {content[:100]}...")

                # 上报用量到当前 run 上下文（供成本统计与预算控制）
                try:
                    from services.usage_tracker import report_usage
                    report_usage(usage)
                except Exception:
                    pass

                return {
                    "content": content,
                    "model": selected_model,
                    "usage": usage,
                }

            except RateLimitError as e:
                wait_time = min(2 ** attempt * 5, 60)
                logger.warning(f"Rate limit hit, waiting {wait_time}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise

            except APITimeoutError as e:
                wait_time = 2 ** attempt * 2
                logger.warning(f"API timeout, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise

            except APIError as e:
                logger.error(f"API error: {e}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2)

    async def chat_stream(
        self,
        messages: List[Dict[str, str]],
        complexity: str = COMPLEXITY_NORMAL,
        model: Optional[str] = None,
        temperature: float = 0.1,
        max_tokens: int = 4096,
        max_retries: int = 3,
    ):
        """流式调用大模型"""
        client = self._get_client()
        selected_model = model or self._select_model(complexity)

        kwargs = {
            "model": selected_model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
        }

        for attempt in range(max_retries):
            try:
                response = await client.chat.completions.create(**kwargs)
                async for chunk in response:
                    content = chunk.choices[0].delta.content
                    if content:
                        yield content
                return

            except RateLimitError as e:
                wait_time = min(2 ** attempt * 5, 60)
                logger.warning(f"Rate limit hit, waiting {wait_time}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise

            except APITimeoutError as e:
                wait_time = 2 ** attempt * 2
                logger.warning(f"API timeout, retrying in {wait_time}s (attempt {attempt + 1}/{max_retries})")
                await asyncio.sleep(wait_time)
                if attempt == max_retries - 1:
                    raise

            except APIError as e:
                logger.error(f"API error: {e}")
                if attempt == max_retries - 1:
                    raise
                await asyncio.sleep(2)

    async def chat_json(
        self,
        messages: List[Dict[str, str]],
        complexity: str = COMPLEXITY_NORMAL,
        print_stream: bool = False,
        stream_log: bool = False,
        **kwargs,
    ) -> Any:
        """
        调用大模型并解析 JSON 响应
        """
        result = await self.chat(
            messages=messages,
            complexity=complexity,
            response_format={"type": "json_object"},
            print_stream=print_stream,
            stream_log=stream_log,
            **kwargs,
        )
        content = result["content"]

        # 尝试直接解析
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            pass

        # 尝试从 markdown 代码块中提取 JSON，或者搜索第一个 '{' 和最后一个 '}'
        import re
        json_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", content, re.DOTALL)
        if json_match:
            try:
                return json.loads(json_match.group(1))
            except json.JSONDecodeError:
                pass
        
        # 兜底：强力搜索大括号区间
        bracket_match = re.search(r"(\{.*\}|\[.*\])", content, re.DOTALL)
        if bracket_match:
            try:
                return json.loads(bracket_match.group(1))
            except json.JSONDecodeError:
                pass

        raise ValueError(f"无法解析 LLM 返回的 JSON:\n{content}")


# 全局单例
llm_gateway = LLMGateway()
