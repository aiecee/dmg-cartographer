"""dmg-cartographer MCP server entry point. Tool modules register on the shared MCP instance in mcp_app."""
import faulthandler

from dmg_cartographer.mcp_app import CONTROLLER, mcp  # noqa: F401  (re-exported for callers)
import dmg_cartographer.tools.investigation  # noqa: F401  (registers investigate_* tools)
import dmg_cartographer.tools.raw  # noqa: F401  (registers raw tools)
import dmg_cartographer.tools.scene  # noqa: F401  (registers the scene tool)
import dmg_cartographer.tools.state_map  # noqa: F401  (registers the state_map tool)


def main() -> None:
    faulthandler.enable()  # native crashes (segfault/abort) dump a traceback to stderr instead of vanishing
    # stdio is ideal for a local agent, and requires no open TCP port.
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()
