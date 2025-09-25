from core.prompts.prompt import SYSTEM_PROMPT_LITE, SYSTEM_PROMPT

# Suna default configuration - simplified and centralized
SUNA_CONFIG = {
    "name": "Suna",
    "description": "Suna is your AI assistant with access to various tools and integrations to help you with tasks across domains.",
    "model": "openai/gpt-5-mini",
    "system_prompt": SYSTEM_PROMPT,
    "configured_mcps": [],
    "custom_mcps": [],
    "agentpress_tools": {
        "sb_shell_tool": True,        # non ti serve
        "sb_files_tool": True,         # ✅ necessario (leggere/scrivere file)
        "sb_deploy_tool": False,       # non serve
        "sb_expose_tool": False,       # non serve
        "web_search_tool": True,      # opzionale (solo se vuoi driver esterni)
        "sb_vision_tool": True,       # no OCR/immagini
        "sb_docs_tool": True,         # no documenti extra
        "sb_image_edit_tool": False,   # no
        "sb_presentation_outline_tool": False,
        "sb_presentation_tool": False,
        "sb_sheets_tool": True,
        "browser_tool": False,         # disabilitato
        "data_providers_tool": False,
        "sb_design_tool": True,
        # "sb_web_dev_tool": False,
        "agent_config_tool": True,     # lascia attivo (serve per gestire l’agente stesso)
        "agent_creation_tool": True,   # idem
        "mcp_search_tool": False,
        "credential_profile_tool": False,
        "workflow_tool": True,
        "trigger_tool": False
    },
    "is_default": True
}

