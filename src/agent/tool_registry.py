import inspect
import logging
from typing import Dict, Any, List, Optional, Callable

from src.agent.shell_tools import redact_secrets

logger = logging.getLogger("gvw.agent.tools")


class Tool:
    """Represents an agent tool with metadata, parameter schema, and execution handler."""

    def __init__(
        self,
        name: str,
        description: str,
        handler: Callable,
        parameters: Optional[Dict[str, Any]] = None,
    ):
        self.name = name
        self.description = description.strip()
        self.handler = handler
        self.parameters = parameters or {}

    def execute(self, **kwargs) -> Any:
        return self.handler(**kwargs)


class ToolRegistry:
    """
    Lightweight, Pi-inspired Tool Registry.
    Supports decorator registration, schema generation, and safe execution.
    """

    def __init__(self):
        self._tools: Dict[str, Tool] = {}

    def register(
        self,
        name: Optional[str] = None,
        description: Optional[str] = None,
        parameters: Optional[Dict[str, Any]] = None,
    ):
        """Decorator or method to register a tool."""

        def decorator(func: Callable):
            tool_name = name or func.__name__
            tool_desc = description or (func.__doc__ or "").strip() or "No description provided."

            # Infer parameters from signature if not provided
            params = parameters
            if params is None:
                params = {}
                sig = inspect.signature(func)
                for param_name, param in sig.parameters.items():
                    if param_name in ("self", "cls"):
                        continue
                    param_type = "string"
                    if param.annotation == int:
                        param_type = "integer"
                    elif param.annotation == bool:
                        param_type = "boolean"
                    elif param.annotation == float:
                        param_type = "number"
                    elif param.annotation == dict:
                        param_type = "object"
                    elif param.annotation == list:
                        param_type = "array"

                    default_val = param.default if param.default != inspect.Parameter.empty else None
                    params[param_name] = {
                        "type": param_type,
                        "default": default_val,
                        "required": param.default == inspect.Parameter.empty,
                    }

            tool_obj = Tool(
                name=tool_name,
                description=tool_desc,
                handler=func,
                parameters=params,
            )
            self._tools[tool_name] = tool_obj
            return func

        return decorator

    def register_tool(
        self,
        name: str,
        description: str,
        handler: Callable,
        parameters: Optional[Dict[str, Any]] = None,
    ) -> Tool:
        """Directly registers a tool instance."""
        tool = Tool(
            name=name,
            description=description,
            handler=handler,
            parameters=parameters,
        )
        self._tools[name] = tool
        return tool

    def get(self, name: str) -> Optional[Tool]:
        return self._tools.get(name)

    def list_tools(self) -> List[Tool]:
        return list(self._tools.values())

    def execute(self, name: str, args: Dict[str, Any]) -> Any:
        """Executes tool safely with exception isolation."""
        tool = self.get(name)
        if not tool:
            return {"status": "error", "error": f"未知工具名稱：'{name}'"}

        try:
            # Filter kwargs to match function signature
            sig = inspect.signature(tool.handler)
            accepted_params = set(sig.parameters.keys())
            has_var_kwargs = any(
                p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
            )

            if has_var_kwargs:
                exec_kwargs = args
            else:
                exec_kwargs = {k: v for k, v in args.items() if k in accepted_params}

            return tool.execute(**exec_kwargs)
        except Exception as e:
            safe_err = redact_secrets(str(e))
            logger.warning("Error executing tool: %s", safe_err, exc_info=True)
            return {"status": "error", "error": f"工具 '{name}' 執行失敗: {safe_err}"}

    def generate_prompt_documentation(self) -> str:
        """Generates clear, concise tool documentation for the LLM system prompt."""
        lines = ["你可以調用以下工具："]
        for idx, tool in enumerate(self._tools.values(), start=1):
            param_str = ", ".join([f"{k}: {v.get('type', 'any')}" for k, v in tool.parameters.items()])
            lines.append(f"{idx}. {tool.name}({param_str}) -> {tool.description}")
        return "\n".join(lines)

    def to_openai_tools(self) -> List[Dict[str, Any]]:
        """Converts tools into OpenAI Function Calling format for native API callers."""
        tools_schema = []
        for tool in self._tools.values():
            properties = {}
            required = []
            for k, v in tool.parameters.items():
                properties[k] = {
                    "type": v.get("type", "string"),
                    "description": v.get("description", k),
                }
                if v.get("required", False):
                    required.append(k)

            tools_schema.append({
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": {
                        "type": "object",
                        "properties": properties,
                        "required": required,
                    },
                },
            })
        return tools_schema

