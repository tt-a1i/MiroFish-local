"""
Report Agent Service
Implements ReACT-mode simulation report generation using LangChain + Zep

Features:
1. Generate reports based on simulation requirements and Zep graph information
2. Plan outline structure first, then generate section by section
3. Each section uses ReACT multi-round thinking and reflection mode
4. Support user chat with autonomous tool retrieval
"""

import os
import json
import time
import re
from typing import Dict, Any, List, Optional, Callable
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum

from ..config import Config
from ..utils.llm_client import LLMClient
from ..utils.logger import get_logger
from .zep_tools import (
    ZepToolsService, 
    SearchResult, 
    InsightForgeResult, 
    PanoramaResult,
    InterviewResult
)

logger = get_logger('mirofish.report_agent')


class ReportLogger:
    """
    Report Agent detailed logger

    Generates agent_log.jsonl file in the report folder, recording each step's detailed actions.
    Each line is a complete JSON object containing timestamp, action type, detailed content, etc.
    """

    def __init__(self, report_id: str):
        """
        Initialize logger

        Args:
            report_id: Report ID, used to determine log file path
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'agent_log.jsonl'
        )
        self.start_time = datetime.now()
        self._ensure_log_file()
    
    def _ensure_log_file(self):
        """Ensure the log file directory exists"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _get_elapsed_time(self) -> float:
        """Get elapsed time from start to now (seconds)"""
        return (datetime.now() - self.start_time).total_seconds()
    
    def log(
        self,
        action: str,
        stage: str,
        details: Dict[str, Any],
        section_title: str = None,
        section_index: int = None
    ):
        """
        Record a log entry

        Args:
            action: Action type, e.g. 'start', 'tool_call', 'llm_response', 'section_complete'
            stage: Current stage, e.g. 'planning', 'generating', 'completed'
            details: Detailed content dictionary, not truncated
            section_title: Current section title (optional)
            section_index: Current section index (optional)
        """
        log_entry = {
            "timestamp": datetime.now().isoformat(),
            "elapsed_seconds": round(self._get_elapsed_time(), 2),
            "report_id": self.report_id,
            "action": action,
            "stage": stage,
            "section_title": section_title,
            "section_index": section_index,
            "details": details
        }
        
        # Append to JSONL file
        with open(self.log_file_path, 'a', encoding='utf-8') as f:
            f.write(json.dumps(log_entry, ensure_ascii=False) + '\n')
    
    def log_start(self, simulation_id: str, graph_id: str, simulation_requirement: str):
        """Record report generation start"""
        self.log(
            action="report_start",
            stage="pending",
            details={
                "simulation_id": simulation_id,
                "graph_id": graph_id,
                "simulation_requirement": simulation_requirement,
                "message": "Report generation task started"
            }
        )
    
    def log_planning_start(self):
        """Record outline planning start"""
        self.log(
            action="planning_start",
            stage="planning",
            details={"message": "Starting report outline planning"}
        )
    
    def log_planning_context(self, context: Dict[str, Any]):
        """Record context information obtained during planning"""
        self.log(
            action="planning_context",
            stage="planning",
            details={
                "message": "Retrieved simulation context information",
                "context": context
            }
        )
    
    def log_planning_complete(self, outline_dict: Dict[str, Any]):
        """Record outline planning completion"""
        self.log(
            action="planning_complete",
            stage="planning",
            details={
                "message": "Outline planning completed",
                "outline": outline_dict
            }
        )
    
    def log_section_start(self, section_title: str, section_index: int):
        """Record section generation start"""
        self.log(
            action="section_start",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={"message": f"Starting section generation: {section_title}"}
        )
    
    def log_react_thought(self, section_title: str, section_index: int, iteration: int, thought: str):
        """Record ReACT thought process"""
        self.log(
            action="react_thought",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "thought": thought,
                "message": f"ReACT thought round {iteration}"
            }
        )
    
    def log_tool_call(
        self,
        section_title: str,
        section_index: int,
        tool_name: str,
        parameters: Dict[str, Any],
        iteration: int
    ):
        """Record tool call"""
        self.log(
            action="tool_call",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "parameters": parameters,
                "message": f"Calling tool: {tool_name}"
            }
        )
    
    def log_tool_result(
        self,
        section_title: str,
        section_index: int,
        tool_name: str,
        result: str,
        iteration: int
    ):
        """Record tool call result (full content, not truncated)"""
        self.log(
            action="tool_result",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "tool_name": tool_name,
                "result": result,  # Full result, not truncated
                "result_length": len(result),
                "message": f"Tool {tool_name} returned result"
            }
        )
    
    def log_llm_response(
        self,
        section_title: str,
        section_index: int,
        response: str,
        iteration: int,
        has_tool_calls: bool,
        has_final_answer: bool
    ):
        """Record LLM response (full content, not truncated)"""
        self.log(
            action="llm_response",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "iteration": iteration,
                "response": response,  # Full response, not truncated
                "response_length": len(response),
                "has_tool_calls": has_tool_calls,
                "has_final_answer": has_final_answer,
                "message": f"LLM response (tool_calls: {has_tool_calls}, final_answer: {has_final_answer})"
            }
        )
    
    def log_section_content(
        self,
        section_title: str,
        section_index: int,
        content: str,
        tool_calls_count: int,
        is_subsection: bool = False
    ):
        """Record section/subsection content generation completion (content only, not full section completion)"""
        action = "subsection_content" if is_subsection else "section_content"
        self.log(
            action=action,
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": content,  # Full content, not truncated
                "content_length": len(content),
                "tool_calls_count": tool_calls_count,
                "is_subsection": is_subsection,
                "message": f"{'Subsection' if is_subsection else 'Section'} {section_title} content generation completed"
            }
        )
    
    def log_section_full_complete(
        self,
        section_title: str,
        section_index: int,
        full_content: str,
        subsection_count: int
    ):
        """
        Record full section generation completion (including all merged subsection content)

        Frontend should monitor this log to determine if a section is truly complete and get full content
        """
        self.log(
            action="section_complete",
            stage="generating",
            section_title=section_title,
            section_index=section_index,
            details={
                "content": full_content,  # Full section content (including subsections), not truncated
                "content_length": len(full_content),
                "subsection_count": subsection_count,
                "message": f"Section {section_title} fully generated (with {subsection_count} subsections)"
            }
        )
    
    def log_report_complete(self, total_sections: int, total_time_seconds: float):
        """Record report generation completion"""
        self.log(
            action="report_complete",
            stage="completed",
            details={
                "total_sections": total_sections,
                "total_time_seconds": round(total_time_seconds, 2),
                "message": "Report generation completed"
            }
        )
    
    def log_error(self, error_message: str, stage: str, section_title: str = None):
        """Record error"""
        self.log(
            action="error",
            stage=stage,
            section_title=section_title,
            section_index=None,
            details={
                "error": error_message,
                "message": f"Error occurred: {error_message}"
            }
        )


class ReportConsoleLogger:
    """
    Report Agent console logger

    Writes console-style logs (INFO, WARNING, etc.) to a console_log.txt file in the report folder.
    These logs differ from agent_log.jsonl — they are plain text console output.
    """

    def __init__(self, report_id: str):
        """
        Initialize console logger

        Args:
            report_id: Report ID, used to determine log file path
        """
        self.report_id = report_id
        self.log_file_path = os.path.join(
            Config.UPLOAD_FOLDER, 'reports', report_id, 'console_log.txt'
        )
        self._ensure_log_file()
        self._file_handler = None
        self._setup_file_handler()
    
    def _ensure_log_file(self):
        """Ensure the log file directory exists"""
        log_dir = os.path.dirname(self.log_file_path)
        os.makedirs(log_dir, exist_ok=True)
    
    def _setup_file_handler(self):
        """Set up file handler to write logs to file simultaneously"""
        import logging

        # Create file handler
        self._file_handler = logging.FileHandler(
            self.log_file_path,
            mode='a',
            encoding='utf-8'
        )
        self._file_handler.setLevel(logging.INFO)

        # Use the same concise format as the console
        formatter = logging.Formatter(
            '[%(asctime)s] %(levelname)s: %(message)s',
            datefmt='%H:%M:%S'
        )
        self._file_handler.setFormatter(formatter)

        # Attach to report_agent related loggers
        loggers_to_attach = [
            'mirofish.report_agent',
            'mirofish.zep_tools',
        ]

        for logger_name in loggers_to_attach:
            target_logger = logging.getLogger(logger_name)
            # Avoid duplicate attachment
            if self._file_handler not in target_logger.handlers:
                target_logger.addHandler(self._file_handler)
    
    def close(self):
        """Close file handler and remove from logger"""
        import logging
        
        if self._file_handler:
            loggers_to_detach = [
                'mirofish.report_agent',
                'mirofish.zep_tools',
            ]
            
            for logger_name in loggers_to_detach:
                target_logger = logging.getLogger(logger_name)
                if self._file_handler in target_logger.handlers:
                    target_logger.removeHandler(self._file_handler)
            
            self._file_handler.close()
            self._file_handler = None
    
    def __del__(self):
        """Ensure file handler is closed on destruction"""
        self.close()


class ReportStatus(str, Enum):
    """Report status"""
    PENDING = "pending"
    PLANNING = "planning"
    GENERATING = "generating"
    COMPLETED = "completed"
    FAILED = "failed"


@dataclass
class ReportSection:
    """Report section"""
    title: str
    content: str = ""
    subsections: List['ReportSection'] = field(default_factory=list)
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "content": self.content,
            "subsections": [s.to_dict() for s in self.subsections]
        }
    
    def to_markdown(self, level: int = 2) -> str:
        """Convert to Markdown format"""
        md = f"{'#' * level} {self.title}\n\n"
        if self.content:
            md += f"{self.content}\n\n"
        for sub in self.subsections:
            md += sub.to_markdown(level + 1)
        return md


@dataclass
class ReportOutline:
    """Report outline"""
    title: str
    summary: str
    sections: List[ReportSection]
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "title": self.title,
            "summary": self.summary,
            "sections": [s.to_dict() for s in self.sections]
        }
    
    def to_markdown(self) -> str:
        """Convert to Markdown format"""
        md = f"# {self.title}\n\n"
        md += f"> {self.summary}\n\n"
        for section in self.sections:
            md += section.to_markdown()
        return md


@dataclass
class Report:
    """Complete report"""
    report_id: str
    simulation_id: str
    graph_id: str
    simulation_requirement: str
    status: ReportStatus
    outline: Optional[ReportOutline] = None
    markdown_content: str = ""
    created_at: str = ""
    completed_at: str = ""
    error: Optional[str] = None
    
    def to_dict(self) -> Dict[str, Any]:
        return {
            "report_id": self.report_id,
            "simulation_id": self.simulation_id,
            "graph_id": self.graph_id,
            "simulation_requirement": self.simulation_requirement,
            "status": self.status.value,
            "outline": self.outline.to_dict() if self.outline else None,
            "markdown_content": self.markdown_content,
            "created_at": self.created_at,
            "completed_at": self.completed_at,
            "error": self.error
        }


class ReportAgent:
    """
    Report Agent - Simulation Report Generation Agent

    Uses ReACT (Reasoning + Acting) mode:
    1. Planning phase: Analyze simulation requirements, plan report outline structure
    2. Generation phase: Generate content section by section, each section may call tools multiple times
    3. Reflection phase: Check content completeness and accuracy

    [Core Retrieval Tools - Optimized]
    - insight_forge: Deep insight retrieval (most powerful, auto-decomposes questions, multi-dimensional retrieval)
    - panorama_search: Broad search (get full picture, including historical/expired content)
    - quick_search: Simple search (quick retrieval)

    [Important] Report Agent must prioritize calling tools to retrieve simulation data, not use its own knowledge!
    """

    # Maximum tool calls per section
    MAX_TOOL_CALLS_PER_SECTION = 5

    # Maximum reflection rounds
    MAX_REFLECTION_ROUNDS = 3
    
    # Maximum tool calls in chat
    MAX_TOOL_CALLS_PER_CHAT = 2
    
    def __init__(
        self,
        graph_id: str,
        simulation_id: str,
        simulation_requirement: str,
        llm_client: Optional[LLMClient] = None,
        zep_tools: Optional[ZepToolsService] = None
    ):
        """
        Initialize Report Agent

        Args:
            graph_id: Graph ID
            simulation_id: Simulation ID
            simulation_requirement: Simulation requirement description
            llm_client: LLM client (optional)
            zep_tools: Zep tools service (optional)
        """
        self.graph_id = graph_id
        self.simulation_id = simulation_id
        self.simulation_requirement = simulation_requirement
        
        self.llm = llm_client or LLMClient()
        self.zep_tools = zep_tools or ZepToolsService()
        
        # Tool definitions
        self.tools = self._define_tools()

        # Logger (initialized in generate_report)
        self.report_logger: Optional[ReportLogger] = None
        # Console logger (initialized in generate_report)
        self.console_logger: Optional[ReportConsoleLogger] = None

        logger.info(f"ReportAgent initialized: graph_id={graph_id}, simulation_id={simulation_id}")
    
    def _define_tools(self) -> Dict[str, Dict[str, Any]]:
        """
        Define available tools

        [Important] These three tools are specifically designed for retrieving information
        from simulation graphs. Must prioritize using these tools to get data,
        not use LLM's own knowledge!
        """
        return {
            "insight_forge": {
                "name": "insight_forge",
                "description": """[Deep Insight Retrieval - Most Powerful Search Tool]
This is our most powerful retrieval function, designed for deep analysis. It will:
1. Automatically decompose your question into multiple sub-questions
2. Retrieve information from the simulation graph across multiple dimensions
3. Integrate results from semantic search, entity analysis, and relationship chain tracking
4. Return the most comprehensive and in-depth retrieval content

[Use Cases]
- Need to deeply analyze a topic
- Need to understand multiple aspects of an event
- Need to gather rich material to support a report section

[Return Content]
- Relevant fact originals (can be cited directly)
- Core entity insights
- Relationship chain analysis""",
                "parameters": {
                    "query": "Question or topic you want to deeply analyze",
                    "report_context": "Current report section context (optional, helps generate more precise sub-questions)"
                },
                "priority": "high"
            },
            "panorama_search": {
                "name": "panorama_search",
                "description": """[Broad Search - Get Full Picture View]
This tool is used to get the complete picture of simulation results, especially suitable for understanding event evolution. It will:
1. Retrieve all related nodes and relationships
2. Distinguish between currently valid facts and historical/expired facts
3. Help you understand how public opinion has evolved

[Use Cases]
- Need to understand the complete development trajectory of an event
- Need to compare public opinion changes across different stages
- Need to get comprehensive entity and relationship information

[Return Content]
- Currently valid facts (latest simulation results)
- Historical/expired facts (evolution records)
- All involved entities""",
                "parameters": {
                    "query": "Search query for relevance ranking",
                    "include_expired": "Whether to include expired/historical content (default True)"
                },
                "priority": "medium"
            },
            "quick_search": {
                "name": "quick_search",
                "description": """[Quick Search - Fast Retrieval]
A lightweight fast retrieval tool, suitable for simple, direct information queries.

[Use Cases]
- Need to quickly find specific information
- Need to verify a fact
- Simple information retrieval

[Return Content]
- List of facts most relevant to the query""",
                "parameters": {
                    "query": "Search query string",
                    "limit": "Number of results to return (optional, default 10)"
                },
                "priority": "low"
            },
            "interview_agents": {
                "name": "interview_agents",
                "description": """[Deep Interview - Real Agent Interviewing (Dual Platform)]
Calls the OASIS simulation environment's interview API to conduct real interviews with running simulated Agents!
This is not LLM simulation — it calls the real interview API to get raw responses from simulated Agents.
By default, interviews are conducted on both Twitter and Reddit platforms simultaneously for more comprehensive perspectives.

Feature flow:
1. Automatically reads persona files to understand all simulated Agents
2. Intelligently selects Agents most relevant to the interview topic (e.g., students, media, officials, etc.)
3. Automatically generates interview questions
4. Calls /api/simulation/interview/batch API to conduct real interviews on both platforms
5. Consolidates all interview results, providing multi-perspective analysis

[Use Cases]
- Need to understand event perspectives from different roles (What do students think? Media? Officials?)
- Need to collect diverse opinions and positions
- Need to get authentic responses from simulated Agents (from the OASIS simulation environment)
- Want to make the report more vivid by including "interview transcripts"

[Return Content]
- Interviewed Agent identity information
- Each Agent's interview responses on both Twitter and Reddit platforms
- Key quotes (can be cited directly)
- Interview summary and perspective comparison

[Important] Requires the OASIS simulation environment to be running to use this feature!""",
                "parameters": {
                    "interview_topic": "Interview topic or requirement description (e.g., 'understand student views on the dormitory formaldehyde incident')",
                    "max_agents": "Maximum number of Agents to interview (optional, default 5)"
                },
                "priority": "high"
            }
        }
    
    def _execute_tool(self, tool_name: str, parameters: Dict[str, Any], report_context: str = "") -> str:
        """
        Execute tool call

        Args:
            tool_name: Tool name
            parameters: Tool parameters
            report_context: Report context (used for InsightForge)

        Returns:
            Tool execution result (text format)
        """
        logger.info(f"Executing tool: {tool_name}, parameters: {parameters}")
        
        try:
            # ========== Core retrieval tools (optimized) ==========

            if tool_name == "insight_forge":
                # Deep insight retrieval - most powerful tool
                query = parameters.get("query", "")
                ctx = parameters.get("report_context", "") or report_context
                result = self.zep_tools.insight_forge(
                    graph_id=self.graph_id,
                    query=query,
                    simulation_requirement=self.simulation_requirement,
                    report_context=ctx
                )
                return result.to_text()
            
            elif tool_name == "panorama_search":
                # Broad search - get full picture
                query = parameters.get("query", "")
                include_expired = parameters.get("include_expired", True)
                if isinstance(include_expired, str):
                    include_expired = include_expired.lower() in ['true', '1', 'yes']
                result = self.zep_tools.panorama_search(
                    graph_id=self.graph_id,
                    query=query,
                    include_expired=include_expired
                )
                return result.to_text()
            
            elif tool_name == "quick_search":
                # Simple search - quick retrieval
                query = parameters.get("query", "")
                limit = parameters.get("limit", 10)
                if isinstance(limit, str):
                    limit = int(limit)
                result = self.zep_tools.quick_search(
                    graph_id=self.graph_id,
                    query=query,
                    limit=limit
                )
                return result.to_text()
            
            elif tool_name == "interview_agents":
                # Deep interview - call real OASIS interview API to get simulated Agent responses (dual platform)
                interview_topic = parameters.get("interview_topic", parameters.get("query", ""))
                max_agents = parameters.get("max_agents", 20)
                if isinstance(max_agents, str):
                    max_agents = int(max_agents)
                result = self.zep_tools.interview_agents(
                    simulation_id=self.simulation_id,
                    interview_requirement=interview_topic,
                    simulation_requirement=self.simulation_requirement,
                    max_agents=max_agents
                )
                return result.to_text()
            
            # ========== Backward-compatible legacy tools (internally redirected to new tools) ==========

            elif tool_name == "search_graph":
                # Redirected to quick_search
                logger.info("search_graph redirected to quick_search")
                return self._execute_tool("quick_search", parameters, report_context)
            
            elif tool_name == "get_graph_statistics":
                result = self.zep_tools.get_graph_statistics(self.graph_id)
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_entity_summary":
                entity_name = parameters.get("entity_name", "")
                result = self.zep_tools.get_entity_summary(
                    graph_id=self.graph_id,
                    entity_name=entity_name
                )
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            elif tool_name == "get_simulation_context":
                # Redirected to insight_forge, as it's more powerful
                logger.info("get_simulation_context redirected to insight_forge")
                query = parameters.get("query", self.simulation_requirement)
                return self._execute_tool("insight_forge", {"query": query}, report_context)
            
            elif tool_name == "get_entities_by_type":
                entity_type = parameters.get("entity_type", "")
                nodes = self.zep_tools.get_entities_by_type(
                    graph_id=self.graph_id,
                    entity_type=entity_type
                )
                result = [n.to_dict() for n in nodes]
                return json.dumps(result, ensure_ascii=False, indent=2)
            
            else:
                return f"Unknown tool: {tool_name}. Please use one of: insight_forge, panorama_search, quick_search"

        except Exception as e:
            logger.error(f"Tool execution failed: {tool_name}, error: {str(e)}")
            return f"Tool execution failed: {str(e)}"
    
    def _parse_tool_calls(self, response: str) -> List[Dict[str, Any]]:
        """
        Parse tool calls from LLM response

        Supported formats:
<tool_call>
        {"name": "tool_name", "parameters": {"param1": "value1"}}
        </tool_call>
        
        Or:
        [TOOL_CALL] tool_name(param1="value1", param2="value2")
        """
        tool_calls = []

        # Format 1: XML style
        xml_pattern = r'<tool_call>\s*(\{.*?\})\s*</tool_call>'
        for match in re.finditer(xml_pattern, response, re.DOTALL):
            try:
                call_data = json.loads(match.group(1))
                tool_calls.append(call_data)
            except json.JSONDecodeError:
                pass
        
        # Format 2: Function call style
        func_pattern = r'\[TOOL_CALL\]\s*(\w+)\s*\((.*?)\)'
        for match in re.finditer(func_pattern, response, re.DOTALL):
            tool_name = match.group(1)
            params_str = match.group(2)
            
            # Parse parameters
            params = {}
            for param_match in re.finditer(r'(\w+)\s*=\s*["\']([^"\']*)["\']', params_str):
                params[param_match.group(1)] = param_match.group(2)
            
            tool_calls.append({
                "name": tool_name,
                "parameters": params
            })
        
        return tool_calls
    
    def _get_tools_description(self) -> str:
        """Generate tool description text"""
        desc_parts = ["Available tools:"]
        for name, tool in self.tools.items():
            params_desc = ", ".join([f"{k}: {v}" for k, v in tool["parameters"].items()])
            desc_parts.append(f"- {name}: {tool['description']}")
            if params_desc:
                desc_parts.append(f"  Parameters: {params_desc}")
        return "\n".join(desc_parts)
    
    def plan_outline(
        self,
        progress_callback: Optional[Callable] = None
    ) -> ReportOutline:
        """
        Plan report outline

        Use LLM to analyze simulation requirements and plan report structure

        Args:
            progress_callback: Progress callback function

        Returns:
            ReportOutline: Report outline
        """
        logger.info("Starting report outline planning...")
        
        if progress_callback:
            progress_callback("planning", 0, "Analyzing simulation requirements...")
        
        # First, get simulation context
        context = self.zep_tools.get_simulation_context(
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement
        )
        
        if progress_callback:
            progress_callback("planning", 30, "Generating report outline...")

        # Build planning prompt
        system_prompt = """You are an expert in writing "Future Prediction Reports" with a "God's-eye view" of the simulated world — you can observe every Agent's behavior, statements, and interactions in the simulation.

[Core Philosophy]
We built a simulated world and injected specific "simulation requirements" as variables. The evolutionary results of the simulated world are predictions of what might happen in the future. What you are observing is not "experimental data" but a "rehearsal of the future."

[Your Task]
Write a "Future Prediction Report" that answers:
1. Under our set conditions, what happened in the future?
2. How did various types of Agents (population groups) react and act?
3. What noteworthy future trends and risks does this simulation reveal?

[Report Positioning]
- ✅ This is a simulation-based future prediction report, revealing "if this happens, what will the future look like"
- ✅ Focus on prediction results: event trajectories, group reactions, emergent phenomena, potential risks
- ✅ Agent statements and behaviors in the simulated world are predictions of future population behavior
- ❌ Not an analysis of the current real-world situation
- ❌ Not a generic public opinion summary

[Section Count Limits]
- Minimum 2 main sections, maximum 5 main sections
- Each section can have 0-2 subsections
- Content should be concise, focused on core prediction findings
- Section structure should be autonomously designed based on prediction results

Please output the report outline in JSON format as follows:
{
    "title": "Report title",
    "summary": "Report summary (one sentence summarizing core prediction findings)",
    "sections": [
        {
            "title": "Section title",
            "description": "Section content description",
            "subsections": [
                {"title": "Subsection title", "description": "Subsection description"}
            ]
        }
    ]
}

Note: The sections array must have a minimum of 2 and a maximum of 5 elements!"""

        user_prompt = f"""[Prediction Scenario Setup]
Variables injected into the simulated world (simulation requirements): {self.simulation_requirement}

[Simulated World Scale]
- Number of entities participating in the simulation: {context.get('graph_statistics', {}).get('total_nodes', 0)}
- Number of relationships between entities: {context.get('graph_statistics', {}).get('total_edges', 0)}
- Entity type distribution: {list(context.get('graph_statistics', {}).get('entity_types', {}).keys())}
- Number of active Agents: {context.get('total_entities', 0)}

[Sample of Simulated Future Facts]
{json.dumps(context.get('related_facts', [])[:10], ensure_ascii=False, indent=2)}

Please examine this future rehearsal from a "God's-eye view":
1. Under our set conditions, what kind of state does the future present?
2. How do various population groups (Agents) react and act?
3. What noteworthy future trends does this simulation reveal?

Based on the prediction results, design the most appropriate report section structure.

[Reminder] Report section count: minimum 2, maximum 5, content should be concise and focused on core prediction findings."""

        try:
            response = self.llm.chat_json(
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": user_prompt}
                ],
                temperature=0.3
            )
            
            if progress_callback:
                progress_callback("planning", 80, "Parsing outline structure...")

            # Parse outline
            sections = []
            for section_data in response.get("sections", []):
                subsections = []
                for sub_data in section_data.get("subsections", []):
                    subsections.append(ReportSection(
                        title=sub_data.get("title", ""),
                        content=""
                    ))
                
                sections.append(ReportSection(
                    title=section_data.get("title", ""),
                    content="",
                    subsections=subsections
                ))
            
            outline = ReportOutline(
                title=response.get("title", "Simulation Analysis Report"),
                summary=response.get("summary", ""),
                sections=sections
            )
            
            if progress_callback:
                progress_callback("planning", 100, "Outline planning completed")

            logger.info(f"Outline planning completed: {len(sections)} sections")
            return outline
            
        except Exception as e:
            logger.error(f"Outline planning failed: {str(e)}")
            # Return default outline (3 sections, as fallback)
            return ReportOutline(
                title="Future Prediction Report",
                summary="Future trends and risk analysis based on simulation predictions",
                sections=[
                    ReportSection(title="Prediction Scenario and Key Findings"),
                    ReportSection(title="Population Behavior Prediction Analysis"),
                    ReportSection(title="Trend Outlook and Risk Warnings")
                ]
            )
    
    def _generate_section_react(
        self,
        section: ReportSection,
        outline: ReportOutline,
        previous_sections: List[str],
        progress_callback: Optional[Callable] = None,
        section_index: int = 0
    ) -> str:
        """
        Generate single section content using ReACT mode

        ReACT loop:
        1. Thought - Analyze what information is needed
        2. Action - Call tools to get information
        3. Observation - Analyze tool results
        4. Repeat until enough information or max iterations reached
        5. Final Answer - Generate section content

        Args:
            section: Section to generate
            outline: Complete outline
            previous_sections: Previous section content (for coherence)
            progress_callback: Progress callback
            section_index: Section index (for logging)

        Returns:
            Section content (Markdown format)
        """
        logger.info(f"ReACT generating section: {section.title}")

        # Record section start log
        if self.report_logger:
            self.report_logger.log_section_start(section.title, section_index)
        
        # Build system prompt - optimized to emphasize tool usage and source citations
        # Determine current section heading level
        section_level = 2  # Default to second-level heading (##)
        sub_heading_level = 3  # Sub-headings use third level (###)
        sub_sub_heading_level = 4  # Smaller sub-headings use fourth level (####)
        
        system_prompt = f"""You are an expert in writing "Future Prediction Reports," currently writing a section of the report.

Report title: {outline.title}
Report summary: {outline.summary}
Prediction scenario (simulation requirement): {self.simulation_requirement}

Current section to write: {section.title}

═══════════════════════════════════════════════════════════════
[Core Philosophy]
═══════════════════════════════════════════════════════════════

The simulated world is a rehearsal of the future. We injected specific conditions (simulation requirements) into the simulated world,
The behavior and interactions of Agents in the simulation are predictions of future population behavior.

Your task is to:
- Reveal what happened in the future under the set conditions
- Predict how various population groups (Agents) react and act
- Discover noteworthy future trends, risks, and opportunities

❌ Do not write an analysis of the current real-world situation
✅ Focus on "what will happen in the future" — simulation results are the predicted future

═══════════════════════════════════════════════════════════════
[Most Important Rules - Must Follow]
═══════════════════════════════════════════════════════════════

1. [Must Call Tools to Observe the Simulated World]
   - You are observing a future rehearsal from a "God's-eye view"
   - All content must come from events and Agent statements in the simulated world
   - Using your own knowledge to write report content is prohibited
   - Each section must call tools at least 2 times (maximum 4) to observe the simulated world, which represents the future

2. [Must Cite Agent's Original Statements and Behaviors]
   - Agent statements and behaviors are predictions of future population behavior
   - Use quote format in the report to present these predictions, for example:
     > "A certain group will state: original content..."
   - These citations are the core evidence of simulation predictions

3. [Faithfully Present Prediction Results]
   - Report content must reflect simulation results in the simulated world that represent the future
   - Do not add information that does not exist in the simulation
   - If information is insufficient in some aspect, state it truthfully

═══════════════════════════════════════════════════════════════
[⚠ Format Standards - Extremely Important!]
═══════════════════════════════════════════════════════════════

[One Section = Minimum Content Unit]
- Each section is the minimum chunk unit of the report
- ❌ Do not use any Markdown headings within the section (#, ##, ###, ####, etc.)
- ❌ Do not add the section main title at the beginning of the content
- ✅ Section titles are added automatically by the system, you only need to write pure body content
- ✅ Use **bold**, paragraph breaks, quotes, and lists to organize content, but do not use headings

[Correct Example]
```
This section analyzes the public opinion propagation dynamics of the event. Through in-depth analysis of simulation data, we found...

**Initial Ignition Phase**

Weibo, as the first scene of public opinion, served the core function of breaking the news:

> "Weibo contributed 68% of the initial volume..."

**Emotion Amplification Phase**

The Douyin platform further amplified the event's impact:

- Strong visual impact
- High emotional resonance
```

[Incorrect Example]
```
## Executive Summary          ← Wrong! Do not add any headings
### 1. Initial Phase     ← Wrong! Do not use ### for sub-sections
#### 1.1 Detailed Analysis   ← Wrong! Do not use #### for subdivisions

This section analyzes...
```

═══════════════════════════════════════════════════════════════
[Available Retrieval Tools] (Call 2-4 times per section)
═══════════════════════════════════════════════════════════════

{self._get_tools_description()}

[Tool Usage Suggestions]
- insight_forge: For deep analysis, automatically decomposes questions and retrieves across multiple dimensions
- panorama_search: For understanding the full picture and evolution process
- quick_search: For quickly verifying specific information
- interview_agents: For interviewing simulated Agents, getting authentic perspectives from different roles

═══════════════════════════════════════════════════════════════
[ReACT Workflow]
═══════════════════════════════════════════════════════════════

1. Thought: [Analyze what information is needed, plan retrieval strategy]
2. Action: [Call tools to get information]
   <tool_call>
   {{"name": "tool_name", "parameters": {{"param_name": "param_value"}}}}
   </tool_call>
3. Observation: [Analyze tool-returned results]
4. Repeat steps 1-3 until enough information is collected (maximum 5 rounds)
5. Final Answer: [Write section content based on retrieval results]

═══════════════════════════════════════════════════════════════
[Section Content Requirements]
═══════════════════════════════════════════════════════════════

1. Content must be based on simulation data retrieved by tools
2. Extensively cite original text to demonstrate simulation effects
3. Use Markdown format (but headings are prohibited):
   - Use **bold text** to highlight key points (instead of sub-headings)
   - Use lists (- or 1. 2. 3.) to organize points
   - Use blank lines to separate different paragraphs
   - ❌ Do not use any heading syntax such as #, ##, ###, ####
4. [Quote Format Standard - Must Be a Separate Paragraph]
   Quotes must stand alone as separate paragraphs, with a blank line before and after, not mixed into a paragraph:
   
   ✅ Correct format:
   ```
   The school's response was considered lacking in substance.
   
   > "The school's coping pattern appeared rigid and slow in the rapidly changing social media environment."
   
   This assessment reflects the public's general dissatisfaction.
   ```
   
   ❌ Incorrect format:
   ```
   The school's response was considered lacking in substance. > "The school's coping pattern..." This assessment reflects...
   ```
5. Maintain logical coherence with other sections
6. [Avoid Repetition] Carefully read the completed section content below, do not repeat the same information
7. [Reiterate] Do not add any headings! Use **bold** instead of sub-section titles"""

        # Build user prompt - pass max 4000 chars per completed section
        if previous_sections:
            previous_parts = []
            for sec in previous_sections:
                # Max 4000 chars per section
                truncated = sec[:4000] + "..." if len(sec) > 4000 else sec
                previous_parts.append(truncated)
            previous_content = "\n\n---\n\n".join(previous_parts)
        else:
            previous_content = "(This is the first section)"
        
        user_prompt = f"""Completed section content (please read carefully to avoid repetition):
{previous_content}

═══════════════════════════════════════════════════════════════
[Current Task] Write section: {section.title}
═══════════════════════════════════════════════════════════════

[Important Reminders]
1. Carefully read the completed sections above, avoid repeating the same content!
2. Must call tools to retrieve simulation data before starting
3. Recommend using insight_forge first for deep retrieval
4. Report content must come from retrieval results, do not use your own knowledge

[⚠ Format Warning - Must Follow]
- ❌ Do not write any headings (#, ##, ###, #### are all prohibited)
- ❌ Do not write "{section.title}" as the beginning
- ✅ Section titles are added automatically by the system
- ✅ Write body text directly, use **bold** instead of sub-section titles

Please start:
1. First think (Thought) about what information this section needs
2. Then call tools (Action) to retrieve simulation data
3. After collecting enough information, output Final Answer (pure body text, no headings at all)"""

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt}
        ]
        
        # ReACT loop
        tool_calls_count = 0
        max_iterations = 5  # Max iteration rounds
        min_tool_calls = 2  # Minimum tool call count

        # Report context, used for InsightForge sub-question generation
        report_context = f"Section title: {section.title}\nSimulation requirement: {self.simulation_requirement}"
        
        for iteration in range(max_iterations):
            if progress_callback:
                progress_callback(
                    "generating", 
                    int((iteration / max_iterations) * 100),
                    f"Deep retrieval and writing ({tool_calls_count}/{self.MAX_TOOL_CALLS_PER_SECTION})"
                )
            
            # Call LLM
            response = self.llm.chat(
                messages=messages,
                temperature=0.5,
                max_tokens=4096
            )
            
            logger.debug(f"LLM response: {response[:200]}...")

            # Check for tool calls and final answer
            has_tool_calls = bool(self._parse_tool_calls(response))
            has_final_answer = "Final Answer:" in response
            
            # Record LLM response log
            if self.report_logger:
                self.report_logger.log_llm_response(
                    section_title=section.title,
                    section_index=section_index,
                    response=response,
                    iteration=iteration + 1,
                    has_tool_calls=has_tool_calls,
                    has_final_answer=has_final_answer
                )
            
            # Check for final answer
            if has_final_answer:
                # If insufficient tool calls, remind to do more retrieval
                if tool_calls_count < min_tool_calls:
                    messages.append({"role": "assistant", "content": response})
                    messages.append({
                        "role": "user", 
                        "content": f"""[Note] You have only called {tool_calls_count} tool(s), the information may be insufficient.

Please call 1-2 more tools to retrieve more simulation data, then output Final Answer.
Suggestions:
- Use insight_forge for deep retrieval of more details
- Use panorama_search to understand the full picture of the event

Remember: Report content must come from simulation results, not your own knowledge!"""
                    })
                    continue
                
                # Extract final answer
                final_answer = response.split("Final Answer:")[-1].strip()
                logger.info(f"Section {section.title} generation completed (tool calls: {tool_calls_count})")

                # Record section content generation completion log (content only, not full section completion)
                # For subsections, section_index >= 100
                is_subsection = section_index >= 100
                if self.report_logger:
                    self.report_logger.log_section_content(
                        section_title=section.title,
                        section_index=section_index,
                        content=final_answer,
                        tool_calls_count=tool_calls_count,
                        is_subsection=is_subsection
                    )
                
                return final_answer
            
            # Parse tool calls
            tool_calls = self._parse_tool_calls(response)
            
            if not tool_calls:
                # No tool calls and no final answer
                messages.append({"role": "assistant", "content": response})
                
                if tool_calls_count < min_tool_calls:
                    # Not enough tool calls yet, strongly prompt to call tools
                    messages.append({
                        "role": "user", 
                        "content": f"""[Important] You have not called enough tools to retrieve simulation data!

Only {tool_calls_count} tool call(s) made so far, at least {min_tool_calls} required.

Please call tools immediately to retrieve information:
<tool_call>
{{"name": "insight_forge", "parameters": {{"query": "{section.title} related simulation results and analysis"}}}}
</tool_call>

[Remember] Report content must be 100% from simulation results, you cannot use your own knowledge!"""
                    })
                else:
                    # Enough calls, can generate final answer
                    messages.append({
                        "role": "user", 
                        "content": "You have retrieved enough simulation data. Please output Final Answer: based on the retrieved information and write the section content.\n\n[Important] Content must extensively cite retrieved original text, using > quote format."
                    })
                continue
            
            # Execute tool calls
            tool_results = []
            for call in tool_calls:
                if tool_calls_count >= self.MAX_TOOL_CALLS_PER_SECTION:
                    break
                
                # Record tool call log
                if self.report_logger:
                    self.report_logger.log_tool_call(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        parameters=call.get("parameters", {}),
                        iteration=iteration + 1
                    )
                
                result = self._execute_tool(
                    call["name"], 
                    call.get("parameters", {}),
                    report_context=report_context
                )
                
                # Record tool result log
                if self.report_logger:
                    self.report_logger.log_tool_result(
                        section_title=section.title,
                        section_index=section_index,
                        tool_name=call["name"],
                        result=result,
                        iteration=iteration + 1
                    )
                
                tool_results.append(f"═══ Tool {call['name']} returned ═══\n{result}")
                tool_calls_count += 1
            
            # Add results to messages
            messages.append({"role": "assistant", "content": response})
            messages.append({
                "role": "user",
                "content": f"""Observation (retrieval results):

{"".join(tool_results)}

═══════════════════════════════════════════════════════════════
[Next Action]
- If information is sufficient: Output Final Answer and write section content (must cite the original text above)
- If more information is needed: Continue calling tools to retrieve data

Tools called {tool_calls_count}/{self.MAX_TOOL_CALLS_PER_SECTION} times
═══════════════════════════════════════════════════════════════"""
            })
        
        # Max iterations reached, force content generation
        logger.warning(f"Section {section.title} reached max iterations, forcing generation")
        messages.append({
            "role": "user",
            "content": "Maximum tool call limit reached, please output Final Answer: directly and generate section content."
        })
        
        response = self.llm.chat(
            messages=messages,
            temperature=0.5,
            max_tokens=4096
        )
        
        if "Final Answer:" in response:
            final_answer = response.split("Final Answer:")[-1].strip()
        else:
            final_answer = response
        
        # Record section content generation completion log (content only, not full section completion)
        is_subsection = section_index >= 100
        if self.report_logger:
            self.report_logger.log_section_content(
                section_title=section.title,
                section_index=section_index,
                content=final_answer,
                tool_calls_count=tool_calls_count,
                is_subsection=is_subsection
            )
        
        return final_answer
    
    def generate_report(
        self,
        progress_callback: Optional[Callable[[str, int, str], None]] = None,
        report_id: Optional[str] = None
    ) -> Report:
        """
        Generate complete report (section-by-section real-time output)

        Each section is saved to the folder immediately after generation, no need to wait for the entire report.
        File structure:
        reports/{report_id}/
            meta.json       - Report metadata
            outline.json    - Report outline
            progress.json   - Generation progress
            section_01.md   - Section 1
            section_02.md   - Section 2
            ...
            full_report.md  - Complete report

        Args:
            progress_callback: Progress callback function (stage, progress, message)
            report_id: Report ID (optional, auto-generated if not provided)

        Returns:
            Report: Complete report
        """
        import uuid

        # If report_id not provided, auto-generate
        if not report_id:
            report_id = f"report_{uuid.uuid4().hex[:12]}"
        start_time = datetime.now()
        
        report = Report(
            report_id=report_id,
            simulation_id=self.simulation_id,
            graph_id=self.graph_id,
            simulation_requirement=self.simulation_requirement,
            status=ReportStatus.PENDING,
            created_at=datetime.now().isoformat()
        )
        
        # Completed section title list (for progress tracking)
        completed_section_titles = []
        
        try:
            # Initialize: create report folder and save initial state
            ReportManager._ensure_report_folder(report_id)
            
            # Initialize logger (structured log agent_log.jsonl)
            self.report_logger = ReportLogger(report_id)
            self.report_logger.log_start(
                simulation_id=self.simulation_id,
                graph_id=self.graph_id,
                simulation_requirement=self.simulation_requirement
            )
            
            # Initialize console logger (console_log.txt)
            self.console_logger = ReportConsoleLogger(report_id)
            
            ReportManager.update_progress(
                report_id, "pending", 0, "Initializing report...",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            # Phase 1: Plan outline
            report.status = ReportStatus.PLANNING
            ReportManager.update_progress(
                report_id, "planning", 5, "Starting report outline planning...",
                completed_sections=[]
            )
            
            # Record planning start log
            self.report_logger.log_planning_start()
            
            if progress_callback:
                progress_callback("planning", 0, "Starting report outline planning...")
            
            outline = self.plan_outline(
                progress_callback=lambda stage, prog, msg: 
                    progress_callback(stage, prog // 5, msg) if progress_callback else None
            )
            report.outline = outline
            
            # Record planning completion log
            self.report_logger.log_planning_complete(outline.to_dict())
            
            # Save outline to file
            ReportManager.save_outline(report_id, outline)
            ReportManager.update_progress(
                report_id, "planning", 15, f"Outline planning completed, {len(outline.sections)} sections total",
                completed_sections=[]
            )
            ReportManager.save_report(report)
            
            logger.info(f"Outline saved to file: {report_id}/outline.json")

            # Phase 2: Generate section by section (save per section)
            report.status = ReportStatus.GENERATING
            
            total_sections = len(outline.sections)
            generated_sections = []  # Save content for context
            
            for i, section in enumerate(outline.sections):
                section_num = i + 1
                base_progress = 20 + int((i / total_sections) * 70)
                
                # Update progress
                ReportManager.update_progress(
                    report_id, "generating", base_progress,
                    f"Generating section: {section.title} ({section_num}/{total_sections})",
                    current_section=section.title,
                    completed_sections=completed_section_titles
                )
                
                if progress_callback:
                    progress_callback(
                        "generating", 
                        base_progress, 
                        f"Generating section: {section.title} ({section_num}/{total_sections})"
                    )
                
                # Generate main section content
                section_content = self._generate_section_react(
                    section=section,
                    outline=outline,
                    previous_sections=generated_sections,
                    progress_callback=lambda stage, prog, msg:
                        progress_callback(
                            stage, 
                            base_progress + int(prog * 0.7 / total_sections),
                            msg
                        ) if progress_callback else None,
                    section_index=section_num
                )
                
                section.content = section_content
                generated_sections.append(f"## {section.title}\n\n{section_content}")
                
                # Generate subsections and merge into main section
                subsection_contents = []
                for j, subsection in enumerate(section.subsections):
                    subsection_num = j + 1
                    
                    if progress_callback:
                        progress_callback(
                            "generating",
                            base_progress + int(((j + 1) / max(len(section.subsections), 1)) * 5),
                            f"Generating subsection: {subsection.title}"
                        )
                    
                    ReportManager.update_progress(
                        report_id, "generating",
                        base_progress + int(((j + 1) / max(len(section.subsections), 1)) * 5),
                        f"Generating subsection: {subsection.title}",
                        current_section=subsection.title,
                        completed_sections=completed_section_titles
                    )
                    
                    subsection_content = self._generate_section_react(
                        section=subsection,
                        outline=outline,
                        previous_sections=generated_sections,
                        progress_callback=None,
                        section_index=section_num * 100 + subsection_num  # Subsection index
                    )
                    subsection.content = subsection_content
                    generated_sections.append(f"### {subsection.title}\n\n{subsection_content}")
                    subsection_contents.append((subsection.title, subsection_content))
                    completed_section_titles.append(f"  └─ {subsection.title}")
                    
                    logger.info(f"Subsection generated: {subsection.title}")
                
                # [Key] Save main section and all subsections into one file
                ReportManager.save_section_with_subsections(
                    report_id, section_num, section, subsection_contents
                )
                completed_section_titles.append(section.title)
                
                # [Important] Record full section completion log, including merged content
                # Build full section content (main section + all subsections)
                full_section_content = f"## {section.title}\n\n{section_content}\n\n"
                for sub_title, sub_content in subsection_contents:
                    full_section_content += f"### {sub_title}\n\n{sub_content}\n\n"
                
                if self.report_logger:
                    self.report_logger.log_section_full_complete(
                        section_title=section.title,
                        section_index=section_num,
                        full_content=full_section_content.strip(),
                        subsection_count=len(subsection_contents)
                    )
                
                logger.info(f"Section saved (with {len(subsection_contents)} subsections): {report_id}/section_{section_num:02d}.md")
                
                # Update progress
                ReportManager.update_progress(
                    report_id, "generating", 
                    base_progress + int(70 / total_sections),
                    f"Section {section.title} completed",
                    current_section=None,
                    completed_sections=completed_section_titles
                )
            
            # Phase 3: Assemble full report
            if progress_callback:
                progress_callback("generating", 95, "Assembling full report...")

            ReportManager.update_progress(
                report_id, "generating", 95, "Assembling full report...",
                completed_sections=completed_section_titles
            )
            
            # Use ReportManager to assemble full report
            report.markdown_content = ReportManager.assemble_full_report(report_id, outline)
            report.status = ReportStatus.COMPLETED
            report.completed_at = datetime.now().isoformat()
            
            # Calculate total elapsed time
            total_time_seconds = (datetime.now() - start_time).total_seconds()
            
            # Record report completion log
            if self.report_logger:
                self.report_logger.log_report_complete(
                    total_sections=total_sections,
                    total_time_seconds=total_time_seconds
                )
            
            # Save final report
            ReportManager.save_report(report)
            ReportManager.update_progress(
                report_id, "completed", 100, "Report generation completed",
                completed_sections=completed_section_titles
            )
            
            if progress_callback:
                progress_callback("completed", 100, "Report generation completed")

            logger.info(f"Report generation completed: {report_id}")
            
            # Close console logger
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
            
        except Exception as e:
            logger.error(f"Report generation failed: {str(e)}")
            report.status = ReportStatus.FAILED
            report.error = str(e)
            
            # Record error log
            if self.report_logger:
                self.report_logger.log_error(str(e), "failed")
            
            # Save failed state
            try:
                ReportManager.save_report(report)
                ReportManager.update_progress(
                    report_id, "failed", -1, f"Report generation failed: {str(e)}",
                    completed_sections=completed_section_titles
                )
            except Exception:
                pass  # Ignore save errors
            
            # Close console logger
            if self.console_logger:
                self.console_logger.close()
                self.console_logger = None
            
            return report
    
    def chat(
        self,
        message: str,
        chat_history: List[Dict[str, str]] = None
    ) -> Dict[str, Any]:
        """
        Chat with Report Agent

        Agent can autonomously call retrieval tools to answer questions during conversation

        Args:
            message: User message
            chat_history: Chat history

        Returns:
            {
                "response": "Agent response",
                "tool_calls": [List of called tools],
                "sources": [Information sources]
            }
        """
        logger.info(f"Report Agent chat: {message[:50]}...")
        
        chat_history = chat_history or []
        
        # Get generated report content
        report_content = ""
        try:
            report = ReportManager.get_report_by_simulation(self.simulation_id)
            if report and report.markdown_content:
                # Limit report length to avoid excessive context
                report_content = report.markdown_content[:15000]
                if len(report.markdown_content) > 15000:
                    report_content += "\n\n... [Report content truncated] ..."
        except Exception as e:
            logger.warning(f"Failed to get report content: {e}")
        
        # Build system prompt
        system_prompt = f"""You are a concise and efficient simulation prediction assistant.

[Background]
Prediction conditions: {self.simulation_requirement}

[Generated Analysis Report]
{report_content if report_content else "(No report yet)"}

[Rules]
1. Prioritize answering questions based on the report content above
2. Answer questions directly, avoid lengthy reasoning
3. Only call tools to retrieve more data when the report content is insufficient to answer
4. Keep answers concise, clear, and well-organized

[Available Tools] (Use only when needed, maximum 1-2 calls)
{self._get_tools_description()}

[Tool Call Format]
<tool_call>
{{"name": "tool_name", "parameters": {{"param_name": "param_value"}}}}
</tool_call>

[Response Style]
- Concise and direct, avoid lengthy exposition
- Use > quote format for key content
- Prioritize conclusions, then explain reasoning"""

        # Build messages
        messages = [{"role": "system", "content": system_prompt}]
        
        # Add chat history
        for h in chat_history[-10:]:  # Limit history length
            messages.append(h)
        
        # Add user message
        messages.append({
            "role": "user", 
            "content": message
        })
        
        # ReACT loop (simplified)
        tool_calls_made = []
        max_iterations = 2  # Reduced iteration rounds
        
        for iteration in range(max_iterations):
            response = self.llm.chat(
                messages=messages,
                temperature=0.5
            )
            
            # Parse tool calls
            tool_calls = self._parse_tool_calls(response)
            
            if not tool_calls:
                # No tool calls, return response directly
                clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', response, flags=re.DOTALL)
                clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
                
                return {
                    "response": clean_response.strip(),
                    "tool_calls": tool_calls_made,
                    "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
                }
            
            # Execute tool calls (limit quantity)
            tool_results = []
            for call in tool_calls[:1]:  # Execute at most 1 tool call per round
                if len(tool_calls_made) >= self.MAX_TOOL_CALLS_PER_CHAT:
                    break
                result = self._execute_tool(call["name"], call.get("parameters", {}))
                tool_results.append({
                    "tool": call["name"],
                    "result": result[:1500]  # Limit result length
                })
                tool_calls_made.append(call)
            
            # Add results to messages
            messages.append({"role": "assistant", "content": response})
            observation = "\n".join([f"[{r['tool']} result]\n{r['result']}" for r in tool_results])
            messages.append({
                "role": "user",
                "content": observation + "\n\nPlease answer the question concisely."
            })
        
        # Reached max iterations, get final response
        final_response = self.llm.chat(
            messages=messages,
            temperature=0.5
        )
        
        # Clean up response
        clean_response = re.sub(r'<tool_call>.*?</tool_call>', '', final_response, flags=re.DOTALL)
        clean_response = re.sub(r'\[TOOL_CALL\].*?\)', '', clean_response)
        
        return {
            "response": clean_response.strip(),
            "tool_calls": tool_calls_made,
            "sources": [tc.get("parameters", {}).get("query", "") for tc in tool_calls_made]
        }


class ReportManager:
    """
    Report Manager

    Handles report persistence storage and retrieval

    File structure (section-by-section output):
    reports/
      {report_id}/
        meta.json          - Report metadata and status
        outline.json       - Report outline
        progress.json      - Generation progress
        section_01.md      - Section 1
        section_02.md      - Section 2
        ...
        full_report.md     - Complete report
    """

    # Report storage directory
    REPORTS_DIR = os.path.join(Config.UPLOAD_FOLDER, 'reports')
    
    @classmethod
    def _ensure_reports_dir(cls):
        """Ensure report root directory exists"""
        os.makedirs(cls.REPORTS_DIR, exist_ok=True)
    
    @classmethod
    def _get_report_folder(cls, report_id: str) -> str:
        """Get report folder path"""
        return os.path.join(cls.REPORTS_DIR, report_id)
    
    @classmethod
    def _ensure_report_folder(cls, report_id: str) -> str:
        """Ensure report folder exists and return path"""
        folder = cls._get_report_folder(report_id)
        os.makedirs(folder, exist_ok=True)
        return folder
    
    @classmethod
    def _get_report_path(cls, report_id: str) -> str:
        """Get report metadata file path"""
        return os.path.join(cls._get_report_folder(report_id), "meta.json")
    
    @classmethod
    def _get_report_markdown_path(cls, report_id: str) -> str:
        """Get full report Markdown file path"""
        return os.path.join(cls._get_report_folder(report_id), "full_report.md")
    
    @classmethod
    def _get_outline_path(cls, report_id: str) -> str:
        """Get outline file path"""
        return os.path.join(cls._get_report_folder(report_id), "outline.json")
    
    @classmethod
    def _get_progress_path(cls, report_id: str) -> str:
        """Get progress file path"""
        return os.path.join(cls._get_report_folder(report_id), "progress.json")
    
    @classmethod
    def _get_section_path(cls, report_id: str, section_index: int) -> str:
        """Get section Markdown file path"""
        return os.path.join(cls._get_report_folder(report_id), f"section_{section_index:02d}.md")
    
    @classmethod
    def _get_agent_log_path(cls, report_id: str) -> str:
        """Get Agent log file path"""
        return os.path.join(cls._get_report_folder(report_id), "agent_log.jsonl")
    
    @classmethod
    def _get_console_log_path(cls, report_id: str) -> str:
        """Get console log file path"""
        return os.path.join(cls._get_report_folder(report_id), "console_log.txt")
    
    @classmethod
    def get_console_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        Get console log content

        These are console output logs (INFO, WARNING, etc.) during report generation,
        different from the structured agent_log.jsonl logs.

        Args:
            report_id: Report ID
            from_line: Line number to start reading from (for incremental retrieval, 0 means from beginning)

        Returns:
            {
                "logs": [List of log lines],
                "total_lines": Total line count,
                "from_line": Starting line number,
                "has_more": Whether more logs exist
            }
        """
        log_path = cls._get_console_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    # Keep original log line, strip trailing newline
                    logs.append(line.rstrip('\n\r'))
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # Read to end
        }
    
    @classmethod
    def get_console_log_stream(cls, report_id: str) -> List[str]:
        """
        Get complete console log (retrieve all at once)

        Args:
            report_id: Report ID

        Returns:
            List of log lines
        """
        result = cls.get_console_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def get_agent_log(cls, report_id: str, from_line: int = 0) -> Dict[str, Any]:
        """
        Get Agent log content

        Args:
            report_id: Report ID
            from_line: Line number to start reading from (for incremental retrieval, 0 means from beginning)

        Returns:
            {
                "logs": [List of log entries],
                "total_lines": Total line count,
                "from_line": Starting line number,
                "has_more": Whether more logs exist
            }
        """
        log_path = cls._get_agent_log_path(report_id)
        
        if not os.path.exists(log_path):
            return {
                "logs": [],
                "total_lines": 0,
                "from_line": 0,
                "has_more": False
            }
        
        logs = []
        total_lines = 0
        
        with open(log_path, 'r', encoding='utf-8') as f:
            for i, line in enumerate(f):
                total_lines = i + 1
                if i >= from_line:
                    try:
                        log_entry = json.loads(line.strip())
                        logs.append(log_entry)
                    except json.JSONDecodeError:
                        # Skip lines that failed to parse
                        continue
        
        return {
            "logs": logs,
            "total_lines": total_lines,
            "from_line": from_line,
            "has_more": False  # Read to end
        }
    
    @classmethod
    def get_agent_log_stream(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        Get complete Agent log (for one-time full retrieval)

        Args:
            report_id: Report ID

        Returns:
            List of log entries
        """
        result = cls.get_agent_log(report_id, from_line=0)
        return result["logs"]
    
    @classmethod
    def save_outline(cls, report_id: str, outline: ReportOutline) -> None:
        """
        Save report outline

        Called immediately after the planning phase completes
        """
        cls._ensure_report_folder(report_id)
        
        with open(cls._get_outline_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(outline.to_dict(), f, ensure_ascii=False, indent=2)
        
        logger.info(f"Outline saved: {report_id}")
    
    @classmethod
    def save_section(
        cls,
        report_id: str,
        section_index: int,
        section: ReportSection,
        is_subsection: bool = False,
        parent_index: int = None
    ) -> str:
        """
        Save a single section (not recommended, use save_section_with_subsections instead)

        Called immediately after each section is generated, for section-by-section output

        Args:
            report_id: Report ID
            section_index: Section index (1-based)
            section: Section object
            is_subsection: Whether this is a subsection
            parent_index: Parent section index (used for subsections)

        Returns:
            Saved file path
        """
        cls._ensure_report_folder(report_id)
        
        # Determine section level and heading format
        if is_subsection and parent_index is not None:
            level = "###"
            file_suffix = f"section_{parent_index:02d}_{section_index:02d}.md"
        else:
            level = "##"
            file_suffix = f"section_{section_index:02d}.md"
        
        # Build section Markdown content - clean up potential duplicate headings
        cleaned_content = cls._clean_section_content(section.content, section.title)
        md_content = f"{level} {section.title}\n\n"
        if cleaned_content:
            md_content += f"{cleaned_content}\n\n"
        
        # Save file
        file_path = os.path.join(cls._get_report_folder(report_id), file_suffix)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.info(f"Section saved: {report_id}/{file_suffix}")
        return file_path
    
    @classmethod
    def save_section_with_subsections(
        cls,
        report_id: str,
        section_index: int,
        section: ReportSection,
        subsection_contents: List[tuple]
    ) -> str:
        """
        Save section and all its subsections to a single file

        Args:
            report_id: Report ID
            section_index: Section index (1-based)
            section: Main section object
            subsection_contents: List of subsections [(title, content), ...]

        Returns:
            Saved file path
        """
        cls._ensure_report_folder(report_id)
        
        # Build main section Markdown content
        cleaned_main_content = cls._clean_section_content(section.content, section.title)
        md_content = f"## {section.title}\n\n"
        if cleaned_main_content:
            md_content += f"{cleaned_main_content}\n\n"
        
        # Add all subsection content
        for sub_title, sub_content in subsection_contents:
            cleaned_sub_content = cls._clean_section_content(sub_content, sub_title)
            md_content += f"### {sub_title}\n\n"
            if cleaned_sub_content:
                md_content += f"{cleaned_sub_content}\n\n"
        
        # Save file
        file_suffix = f"section_{section_index:02d}.md"
        file_path = os.path.join(cls._get_report_folder(report_id), file_suffix)
        with open(file_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.info(f"Section saved (with {len(subsection_contents)} subsections): {report_id}/{file_suffix}")
        return file_path
    
    @classmethod
    def _clean_section_content(cls, content: str, section_title: str) -> str:
        """
        Clean section content

        1. Remove Markdown heading lines at the start that duplicate the section title
        2. Convert all ### and lower-level headings to bold text

        Args:
            content: Original content
            section_title: Section title

        Returns:
            Cleaned content
        """
        import re
        
        if not content:
            return content
        
        content = content.strip()
        lines = content.split('\n')
        cleaned_lines = []
        skip_next_empty = False
        
        for i, line in enumerate(lines):
            stripped = line.strip()
            
            # Check if this is a Markdown heading line
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title_text = heading_match.group(2).strip()
                
                # Check if heading duplicates section title (skip within first 5 lines)
                if i < 5:
                    if title_text == section_title or title_text.replace(' ', '') == section_title.replace(' ', ''):
                        skip_next_empty = True
                        continue
                
                # Convert all heading levels (#, ##, ###, #### etc.) to bold
                # Since section titles are added by the system, content should not have any headings
                cleaned_lines.append(f"**{title_text}**")
                cleaned_lines.append("")  # Add blank line
                continue
            
            # If previous line was a skipped heading and current line is empty, also skip
            if skip_next_empty and stripped == '':
                skip_next_empty = False
                continue
            
            skip_next_empty = False
            cleaned_lines.append(line)
        
        # Remove leading blank lines
        while cleaned_lines and cleaned_lines[0].strip() == '':
            cleaned_lines.pop(0)
        
        # Remove leading horizontal rules
        while cleaned_lines and cleaned_lines[0].strip() in ['---', '***', '___']:
            cleaned_lines.pop(0)
            # Also remove blank lines after the horizontal rule
            while cleaned_lines and cleaned_lines[0].strip() == '':
                cleaned_lines.pop(0)
        
        return '\n'.join(cleaned_lines)
    
    @classmethod
    def update_progress(
        cls,
        report_id: str,
        status: str,
        progress: int,
        message: str,
        current_section: str = None,
        completed_sections: List[str] = None
    ) -> None:
        """
        Update report generation progress

        Frontend can read progress.json for real-time progress
        """
        cls._ensure_report_folder(report_id)
        
        progress_data = {
            "status": status,
            "progress": progress,
            "message": message,
            "current_section": current_section,
            "completed_sections": completed_sections or [],
            "updated_at": datetime.now().isoformat()
        }
        
        with open(cls._get_progress_path(report_id), 'w', encoding='utf-8') as f:
            json.dump(progress_data, f, ensure_ascii=False, indent=2)
    
    @classmethod
    def get_progress(cls, report_id: str) -> Optional[Dict[str, Any]]:
        """Get report generation progress"""
        path = cls._get_progress_path(report_id)
        
        if not os.path.exists(path):
            return None
        
        with open(path, 'r', encoding='utf-8') as f:
            return json.load(f)
    
    @classmethod
    def get_generated_sections(cls, report_id: str) -> List[Dict[str, Any]]:
        """
        Get list of generated sections

        Returns file info for all saved section files
        """
        folder = cls._get_report_folder(report_id)
        
        if not os.path.exists(folder):
            return []
        
        sections = []
        for filename in sorted(os.listdir(folder)):
            if filename.startswith('section_') and filename.endswith('.md'):
                file_path = os.path.join(folder, filename)
                with open(file_path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                # Parse section index from filename
                parts = filename.replace('.md', '').split('_')
                section_index = int(parts[1])
                subsection_index = int(parts[2]) if len(parts) > 2 else None
                
                sections.append({
                    "filename": filename,
                    "section_index": section_index,
                    "subsection_index": subsection_index,
                    "content": content,
                    "is_subsection": subsection_index is not None
                })
        
        return sections
    
    @classmethod
    def assemble_full_report(cls, report_id: str, outline: ReportOutline) -> str:
        """
        Assemble full report

        Assemble full report from saved section files and clean up headings
        """
        folder = cls._get_report_folder(report_id)
        
        # Build report header
        md_content = f"# {outline.title}\n\n"
        md_content += f"> {outline.summary}\n\n"
        md_content += f"---\n\n"
        
        # Read all section files in order (only main section files, not subsection files)
        sections = cls.get_generated_sections(report_id)
        for section_info in sections:
            # Skip subsection files (already merged into main section)
            if section_info.get("is_subsection", False):
                continue
            md_content += section_info["content"]
        
        # Post-processing: clean up heading issues in the entire report
        md_content = cls._post_process_report(md_content, outline)
        
        # Save full report
        full_path = cls._get_report_markdown_path(report_id)
        with open(full_path, 'w', encoding='utf-8') as f:
            f.write(md_content)
        
        logger.info(f"Full report assembled: {report_id}")
        return md_content
    
    @classmethod
    def _post_process_report(cls, content: str, outline: ReportOutline) -> str:
        """
        Post-process report content

        1. Remove duplicate headings
        2. Keep report main title (#) and section headings (##), remove other level headings (###, #### etc.)
        3. Clean up excess blank lines and horizontal rules

        Args:
            content: Original report content
            outline: Report outline

        Returns:
            Processed content
        """
        import re
        
        lines = content.split('\n')
        processed_lines = []
        prev_was_heading = False
        
        # Collect all section titles from the outline
        section_titles = set()
        for section in outline.sections:
            section_titles.add(section.title)
            for sub in section.subsections:
                section_titles.add(sub.title)
        
        i = 0
        while i < len(lines):
            line = lines[i]
            stripped = line.strip()
            
            # Check if this is a heading line
            heading_match = re.match(r'^(#{1,6})\s+(.+)$', stripped)
            
            if heading_match:
                level = len(heading_match.group(1))
                title = heading_match.group(2).strip()
                
                # Check if this is a duplicate heading (same content appearing within 5 consecutive lines)
                is_duplicate = False
                for j in range(max(0, len(processed_lines) - 5), len(processed_lines)):
                    prev_line = processed_lines[j].strip()
                    prev_match = re.match(r'^(#{1,6})\s+(.+)$', prev_line)
                    if prev_match:
                        prev_title = prev_match.group(2).strip()
                        if prev_title == title:
                            is_duplicate = True
                            break
                
                if is_duplicate:
                    # Skip duplicate heading and blank lines after it
                    i += 1
                    while i < len(lines) and lines[i].strip() == '':
                        i += 1
                    continue
                
                # Heading level processing:
                # - # (level=1) Keep only report main title
                # - ## (level=2) Keep section headings
                # - ### and below (level>=3) Convert to bold text
                
                if level == 1:
                    if title == outline.title:
                        # Keep report main title
                        processed_lines.append(line)
                        prev_was_heading = True
                    elif title in section_titles:
                        # Section heading incorrectly uses #, correct to ##
                        processed_lines.append(f"## {title}")
                        prev_was_heading = True
                    else:
                        # Other level-1 headings converted to bold
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                elif level == 2:
                    if title in section_titles or title == outline.title:
                        # Keep section headings
                        processed_lines.append(line)
                        prev_was_heading = True
                    else:
                        # Non-section level-2 headings converted to bold
                        processed_lines.append(f"**{title}**")
                        processed_lines.append("")
                        prev_was_heading = False
                else:
                    # Level-3 and below headings converted to bold text
                    processed_lines.append(f"**{title}**")
                    processed_lines.append("")
                    prev_was_heading = False
                
                i += 1
                continue
            
            elif stripped == '---' and prev_was_heading:
                # Skip horizontal rules immediately after headings
                i += 1
                continue
            
            elif stripped == '' and prev_was_heading:
                # Keep only one blank line after headings
                if processed_lines and processed_lines[-1].strip() != '':
                    processed_lines.append(line)
                prev_was_heading = False
            
            else:
                processed_lines.append(line)
                prev_was_heading = False
            
            i += 1
        
        # Clean up consecutive blank lines (keep at most 2)
        result_lines = []
        empty_count = 0
        for line in processed_lines:
            if line.strip() == '':
                empty_count += 1
                if empty_count <= 2:
                    result_lines.append(line)
            else:
                empty_count = 0
                result_lines.append(line)
        
        return '\n'.join(result_lines)
    
    @classmethod
    def save_report(cls, report: Report) -> None:
        """Save report metadata and full report"""
        cls._ensure_report_folder(report.report_id)
        
        # Save metadata JSON
        with open(cls._get_report_path(report.report_id), 'w', encoding='utf-8') as f:
            json.dump(report.to_dict(), f, ensure_ascii=False, indent=2)
        
        # Save outline
        if report.outline:
            cls.save_outline(report.report_id, report.outline)
        
        # Save full Markdown report
        if report.markdown_content:
            with open(cls._get_report_markdown_path(report.report_id), 'w', encoding='utf-8') as f:
                f.write(report.markdown_content)
        
        logger.info(f"Report saved: {report.report_id}")
    
    @classmethod
    def get_report(cls, report_id: str) -> Optional[Report]:
        """Get report"""
        path = cls._get_report_path(report_id)
        
        if not os.path.exists(path):
            # Compatible with old format: check files stored directly in reports directory
            old_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
            if os.path.exists(old_path):
                path = old_path
            else:
                return None
        
        with open(path, 'r', encoding='utf-8') as f:
            data = json.load(f)
        
        # Rebuild Report object
        outline = None
        if data.get('outline'):
            outline_data = data['outline']
            sections = []
            for s in outline_data.get('sections', []):
                subsections = [
                    ReportSection(title=sub['title'], content=sub.get('content', ''))
                    for sub in s.get('subsections', [])
                ]
                sections.append(ReportSection(
                    title=s['title'],
                    content=s.get('content', ''),
                    subsections=subsections
                ))
            outline = ReportOutline(
                title=outline_data['title'],
                summary=outline_data['summary'],
                sections=sections
            )
        
        # If markdown_content is empty, try reading from full_report.md
        markdown_content = data.get('markdown_content', '')
        if not markdown_content:
            full_report_path = cls._get_report_markdown_path(report_id)
            if os.path.exists(full_report_path):
                with open(full_report_path, 'r', encoding='utf-8') as f:
                    markdown_content = f.read()
        
        return Report(
            report_id=data['report_id'],
            simulation_id=data['simulation_id'],
            graph_id=data['graph_id'],
            simulation_requirement=data['simulation_requirement'],
            status=ReportStatus(data['status']),
            outline=outline,
            markdown_content=markdown_content,
            created_at=data.get('created_at', ''),
            completed_at=data.get('completed_at', ''),
            error=data.get('error')
        )
    
    @classmethod
    def get_report_by_simulation(cls, simulation_id: str) -> Optional[Report]:
        """Get report by simulation ID"""
        cls._ensure_reports_dir()
        
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # New format: directory
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report and report.simulation_id == simulation_id:
                    return report
            # Compatible with old format: JSON file
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report and report.simulation_id == simulation_id:
                    return report
        
        return None
    
    @classmethod
    def list_reports(cls, simulation_id: Optional[str] = None, limit: int = 50) -> List[Report]:
        """List reports"""
        cls._ensure_reports_dir()
        
        reports = []
        for item in os.listdir(cls.REPORTS_DIR):
            item_path = os.path.join(cls.REPORTS_DIR, item)
            # New format: directory
            if os.path.isdir(item_path):
                report = cls.get_report(item)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
            # Compatible with old format: JSON file
            elif item.endswith('.json'):
                report_id = item[:-5]
                report = cls.get_report(report_id)
                if report:
                    if simulation_id is None or report.simulation_id == simulation_id:
                        reports.append(report)
        
        # Sort by creation time in reverse order
        reports.sort(key=lambda r: r.created_at, reverse=True)
        
        return reports[:limit]
    
    @classmethod
    def delete_report(cls, report_id: str) -> bool:
        """Delete report (entire directory)"""
        import shutil
        
        folder_path = cls._get_report_folder(report_id)
        
        # New format: delete entire directory
        if os.path.exists(folder_path) and os.path.isdir(folder_path):
            shutil.rmtree(folder_path)
            logger.info(f"Report directory deleted: {report_id}")
            return True
        
        # Compatible with old format: delete individual files
        deleted = False
        old_json_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.json")
        old_md_path = os.path.join(cls.REPORTS_DIR, f"{report_id}.md")
        
        if os.path.exists(old_json_path):
            os.remove(old_json_path)
            deleted = True
        if os.path.exists(old_md_path):
            os.remove(old_md_path)
            deleted = True
        
        return deleted
