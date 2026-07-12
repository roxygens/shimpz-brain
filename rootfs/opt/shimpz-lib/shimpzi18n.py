"""The i18n rendering leaf (Phase D1): the pt/en string table + the status-line mapper.

STATELESS by design — it renders a key in a language the CALLER passes; it holds no per-chat state
(LANG_PREF and the chat->language resolution `_lang_for` stay in shimpz-gateway). This lets BOTH the
gateway and the co-resident run core (`shimpzchat.py`, Phase D1) render user-facing text without either
importing the other or sharing mutable state across a reload. The gateway keeps `_t(key, chat_id=...)`
/ `_status_for(...)` as thin wrappers that resolve the chat's language and delegate here; the run core
is handed the resolved `lang` and calls `t()` / `status_for()` directly.
"""

_UI_EN = {
    # status ticker (_status_for)
    "status_looking": "📸 looking at the screen…",
    "status_typing": "⌨️ typing…",
    "status_mouse": "🖱️ controlling the mouse…",
    "status_notifying": "✉️ notifying you…",
    "status_need_you": "🙋 I need you…",
    "status_searching": "🌐 searching…",
    "status_editing": "✍️ editing files…",
    "status_reading": "📂 reading files…",
    "status_running": "⚙️ running…",
    "status_tool_fallback": "🛠️ {}…",
    # buttons / keyboards
    "btn_stop": "🛑 Stop",
    "btn_retry": "🔁 Retry",
    "btn_continue": "▶️ Continue",
    "btn_halt": "✋ Stop",
    "btn_desktop": "🖥️ Desktop",
    "btn_files": "📁 Files",
    "btn_new_conversation": "🔄 New conversation",
    "btn_status": "ℹ️ Status",
    "btn_voice_reply": "🔊 Voice reply: {}",
    "btn_answer_myself": "✍️ Answer it myself",
    "btn_open_desktop": "🖥️ Open desktop",
    "btn_resolved": "✅ Resolved",
    "btn_cancel": "❌ Cancel",
    "btn_approve": "✅ Approve",
    "btn_deny": "❌ Deny",
    # cards
    "ask_body": "❓ {}",
    "ask_default_note": "\n\n⭐ = my suggestion — if you don't answer in ~{}m, I'll go with it.",
    "captcha_tail": "Solve it on the desktop (same IP/session) and tap ✅ Resolved.",
    "captcha_body": "🔐 CAPTCHA / manual step\n\n{}\n\n{}",
    "approval_body": "⚠️ Approval\n\nShimpz wants:\n{}",
    "ask_free_suffix": "\n\n✍️ Send the answer in the next message.",
    "ask_resolve_fail_suffix": "\n\n⚠️ couldn't resolve that — ask again",
    "ask_expired_moved_on": "⌛ expired (Shimpz already moved on)",
    "decision_expired_timed_out": "⌛ expired (Shimpz already timed out)",
    "decision_approved": "✅ Approved",
    "decision_denied": "❌ Denied",
    "decision_resolved": "✅ Resolved",
    "decision_cancelled": "❌ Cancelled",
    # callback toasts
    "toast_stopping": "🛑 Stopping…",
    "toast_retrying": "🔁 Retrying…",
    "toast_continuing": "▶️ Continuing…",
    "toast_stopped": "✋ Stopped",
    # command replies
    "access_denied": "⛔ Access denied.",
    "start_msg": "🕶️ Shimpz online. Talk by text or audio, or use /menu.",
    "menu_title": "🕶️ Shimpz — menu",
    "desktop_msg": "🖥️ Live desktop (solve CAPTCHA / take control):",
    "reset_msg": "🔄 Conversation reset (new context).",
    "clear_msg": "🧹 Chat wiped — fresh start, new context.\n(Telegram only lets me delete messages newer than 48h.)",
    "nothing_running": "🛑 Nothing is running right now.",
    "stopped_msg": "🛑 Stopped.",
    "done_msg": "✅ Done.",
    "no_result_msg": "⚠️ That didn't produce a result.",
    "stopped_here_msg": "✋ Ok, I stopped here.",
    "nothing_to_retry": "Nothing to retry.",
    # run outcome text
    "step_limit_msg": ("⏸️ I stopped at this round's step limit (cost control). Continue from where I left off?"),
    "took_too_long_msg": "⏱️ I took too long and stopped.",
    "auto_continue_msg": "⏭️ Long task — continuing on my own ({}/{})…",
    "restarted_msg": "🔄 I was restarted mid-task — resend your last message and I'll pick it back up.",
    "no_response_msg": "⚠️ No response from the brain (exit {}).",
    "couldnt_start_brain": "⚠️ Couldn't start the brain: {}",
    "handler_error_msg": "⚠️ Something errored here processing this: {}",
    "spoken_excerpt": "🔊 spoken excerpt (full text above)",
    "full_reply_caption": "📄 full reply",
    # /status card (labels only — values stay interpolated as-is)
    "status_card": (
        "🕶️ Shimpz online — up {}h{}m\n"
        "Model: {}\n"
        "Running a task here: {}\n"
        "This chat context: ~{}KB / {}KB\n"
        "Active sessions: {}\n"
        "Voice: {} · reply voice: {}"
    ),
    # /menu files card
    "files_header": "📁 Workspace:\n",
    "files_empty": "(empty)",
    # voice / intake errors
    "stt_not_configured": "🎙️ I received audio but STT is not configured.",
    "audio_fetch_fail": "🎙️ I couldn't fetch that audio — please send it again.",
    "audio_understand_fail": "🎙️ I couldn't understand the audio.",
    "image_download_fail": "🖼️ I couldn't download that image — please send it again.",
    "doc_download_fail": "📎 I couldn't download that file — please send it again.",
    "answer_sent": "✅ Answer sent to Shimpz.",
    "ask_expired_new_msg": "⌛ That question expired — Shimpz already moved on. Treating this as a new message.",
    # /login flow — EXCEPTION: this is NEWLY AUTHORED English (the existing code was pt-BR only)
    "login_prompt": (
        "🔐 To reconnect Shimpz (Claude Code login):\n\n"
        "1) Open this link and sign in to your Anthropic account:\n{}\n\n"
        "2) Copy the code that appears and paste it here in your NEXT message."
    ),
    "login_auth_hint": ("\n\n🔐 Looks like Shimpz is logged out of Claude Code. Use /login to reconnect via Telegram."),
    "login_in_progress": (
        "🔐 A /login is already in progress — paste the code here in your next message (or wait for it to expire)."
    ),
    "login_already_logged_in": "✅ Shimpz is already logged in ({}).",
    "login_start_fail": "⚠️ Couldn't start the login: {}",
    "login_starting": "🔐 Starting Claude Code login… fetching the link.",
    "login_no_link": "⚠️ Couldn't get the login link. Try /login again.",
    "login_code_send_fail": "⚠️ Couldn't send the code: {}",
    "login_code_received": "🔐 Code received, finishing login…",
    "login_result_timeout": "⚠️ The login didn't respond in time. Try /login again.",
    "login_done": "✅ Login complete — {}.",
    "login_failed": "❌ Login failed: {}\nTry /login again.",
    # /lang command (R99: live, per-chat language switch — see LANG_PREF)
    "lang_prompt": "🌐 Choose your language:",
    "lang_btn_pt": "🇧🇷 Português",
    "lang_btn_en": "🇺🇸 English",
    "lang_set_confirm": "✅ Language set: English",
    # BotCommand descriptions (Telegram's `/` command menu)
    "cmd_menu_desc": "open the menu",
    "cmd_desktop_desc": "open the live desktop",
    "cmd_login_desc": "log Shimpz into Claude Code",
    "cmd_reset_desc": "reset the conversation",
    "cmd_clear_desc": "wipe the chat history",
    "cmd_start_desc": "start",
    "cmd_lang_desc": "choose pt/en for this chat",
    # live-card phases + heartbeat (R106: formerly hardcoded literals that bypassed _t())
    "status_thinking": "🤔 thinking…",
    "status_working": "⏳ working",
    "heartbeat_suffix": "{} · still on it…",
    "continuing_banner": "▶️ Continuing from where I left off…",
    "checkpointing": "🧠 checkpointing context…",
    # callback toast for a malformed/stale button payload
    "stale_button": "stale button",
    # /status card interpolated values + voice-pref display labels
    "word_yes": "yes",
    "word_no": "no",
    "word_on": "on",
    "word_off": "off",
    "voice_auto_hint": "auto (when you send audio)",
    "voice_auto": "auto",
    "voice_always": "always",
    "voice_never": "never",
    # shimpz-login bridge result codes ({"code","args"} in the result file — see _login_result_text)
    "login_res_logged_in_as": "logged in as {}",
    "login_res_logged_in": "logged in",
    "login_res_cli_output": "{}",
    "login_res_failed_exit": "login failed (exit {})",
    "login_res_timeout": "login timed out (no code received in {}s)",
    "login_res_error": "login error: {}",
    "login_res_ended_unexpectedly": "login ended unexpectedly",
}


_UI_PT = {
    "status_looking": "📸 olhando para a tela…",
    "status_typing": "⌨️ digitando…",
    "status_mouse": "🖱️ controlando o mouse…",
    "status_notifying": "✉️ avisando você…",
    "status_need_you": "🙋 preciso de você…",
    "status_searching": "🌐 pesquisando…",
    "status_editing": "✍️ editando arquivos…",
    "status_reading": "📂 lendo arquivos…",
    "status_running": "⚙️ rodando…",
    "status_tool_fallback": "🛠️ {}…",
    "btn_stop": "🛑 Parar",
    "btn_retry": "🔁 Repetir",
    "btn_continue": "▶️ Continuar",
    "btn_halt": "✋ Parar",
    "btn_desktop": "🖥️ Desktop",
    "btn_files": "📁 Arquivos",
    "btn_new_conversation": "🔄 Nova conversa",
    "btn_status": "ℹ️ Status",
    "btn_voice_reply": "🔊 Resposta por voz: {}",
    "btn_answer_myself": "✍️ Responder eu mesmo",
    "btn_open_desktop": "🖥️ Abrir desktop",
    "btn_resolved": "✅ Resolvido",
    "btn_cancel": "❌ Cancelar",
    "btn_approve": "✅ Aprovar",
    "btn_deny": "❌ Negar",
    "ask_body": "❓ {}",
    "ask_default_note": "\n\n⭐ = minha sugestão — se você não responder em ~{}m, eu vou com ela.",
    "captcha_tail": "Resolva no desktop (mesmo IP/sessão) e toque em ✅ Resolvido.",
    "captcha_body": "🔐 CAPTCHA / passo manual\n\n{}\n\n{}",
    "approval_body": "⚠️ Aprovação\n\nShimpz quer:\n{}",
    "ask_free_suffix": "\n\n✍️ Envie a resposta na próxima mensagem.",
    "ask_resolve_fail_suffix": "\n\n⚠️ não consegui resolver isso — pergunte de novo",
    "ask_expired_moved_on": "⌛ expirou (Shimpz já seguiu em frente)",
    "decision_expired_timed_out": "⌛ expirou (Shimpz já teve timeout)",
    "decision_approved": "✅ Aprovado",
    "decision_denied": "❌ Negado",
    "decision_resolved": "✅ Resolvido",
    "decision_cancelled": "❌ Cancelado",
    "toast_stopping": "🛑 Parando…",
    "toast_retrying": "🔁 Repetindo…",
    "toast_continuing": "▶️ Continuando…",
    "toast_stopped": "✋ Parado",
    "access_denied": "⛔ Acesso negado.",
    "start_msg": "🕶️ Shimpz online. Fale por texto ou áudio, ou use /menu.",
    "menu_title": "🕶️ Shimpz — menu",
    "desktop_msg": "🖥️ Desktop ao vivo (resolva CAPTCHA / assuma o controle):",
    "reset_msg": "🔄 Conversa reiniciada (novo contexto).",
    "clear_msg": (
        "🧹 Conversa limpa — recomeço do zero, novo contexto.\n"
        "(O Telegram só me deixa apagar mensagens com menos de 48h.)"
    ),
    "nothing_running": "🛑 Nada rodando agora.",
    "stopped_msg": "🛑 Parado.",
    "done_msg": "✅ Feito.",
    "no_result_msg": "⚠️ Isso não produziu um resultado.",
    "stopped_here_msg": "✋ Ok, parei por aqui.",
    "nothing_to_retry": "Nada para repetir.",
    "step_limit_msg": ("⏸️ Parei no limite de passos desta rodada (controle de custo). Continuar de onde parei?"),
    "took_too_long_msg": "⏱️ Demorei demais e parei.",
    "auto_continue_msg": "⏭️ Tarefa longa — continuando por conta própria ({}/{})…",
    "restarted_msg": "🔄 Fui reiniciado no meio da tarefa — reenvie sua última mensagem que eu continuo.",
    "no_response_msg": "⚠️ Sem resposta do cérebro (saída {}).",
    "couldnt_start_brain": "⚠️ Não consegui iniciar o cérebro: {}",
    "handler_error_msg": "⚠️ Algo deu erro aqui processando isso: {}",
    "spoken_excerpt": "🔊 trecho falado (texto completo acima)",
    "full_reply_caption": "📄 resposta completa",
    "status_card": (
        "🕶️ Shimpz online — ativo há {}h{}m\n"
        "Modelo: {}\n"
        "Rodando uma tarefa aqui: {}\n"
        "Contexto deste chat: ~{}KB / {}KB\n"
        "Sessões ativas: {}\n"
        "Voz: {} · voz de resposta: {}"
    ),
    "files_header": "📁 Workspace:\n",
    "files_empty": "(vazio)",
    "stt_not_configured": "🎙️ Recebi áudio mas o STT não está configurado.",
    "audio_fetch_fail": "🎙️ Não consegui buscar esse áudio — envie de novo, por favor.",
    "audio_understand_fail": "🎙️ Não consegui entender o áudio.",
    "image_download_fail": "🖼️ Não consegui baixar essa imagem — envie de novo, por favor.",
    "doc_download_fail": "📎 Não consegui baixar esse arquivo — envie de novo, por favor.",
    "answer_sent": "✅ Resposta enviada para o Shimpz.",
    "ask_expired_new_msg": (
        "⌛ Essa pergunta expirou — o Shimpz já seguiu em frente. Tratando isso como uma nova mensagem."
    ),
    # /login flow — EXCEPTION: this is the EXISTING pt-BR text, kept verbatim
    "login_prompt": (
        "🔐 Para reconectar o Shimpz (login do Claude Code):\n\n"
        "1) Abra este link e entre na sua conta Anthropic:\n{}\n\n"
        "2) Copie o código que aparecer e cole aqui na PRÓXIMA mensagem."
    ),
    "login_auth_hint": (
        "\n\n🔐 Parece que o Shimpz está deslogado do Claude Code. Use /login para reconectar pelo Telegram."
    ),
    "login_in_progress": (
        "🔐 Já tem um /login em andamento — cole o código aqui na próxima mensagem (ou aguarde expirar)."
    ),
    "login_already_logged_in": "✅ Shimpz já está logado ({}).",
    "login_start_fail": "⚠️ Não consegui iniciar o login: {}",
    "login_starting": "🔐 Iniciando login do Claude Code… buscando o link.",
    "login_no_link": "⚠️ Não consegui obter o link de login. Tente /login de novo.",
    "login_code_send_fail": "⚠️ Não consegui enviar o código: {}",
    "login_code_received": "🔐 Código recebido, finalizando login…",
    "login_result_timeout": "⚠️ O login não respondeu a tempo. Tente /login de novo.",
    "login_done": "✅ Login concluído — {}.",
    "login_failed": "❌ Login falhou: {}\nTente /login de novo.",
    # /lang command (R99: live, per-chat language switch — see LANG_PREF)
    "lang_prompt": "🌐 Escolha seu idioma:",
    "lang_btn_pt": "🇧🇷 Português",
    "lang_btn_en": "🇺🇸 English",
    "lang_set_confirm": "✅ Idioma definido: Português",
    "cmd_menu_desc": "abrir o menu",
    "cmd_desktop_desc": "abrir o desktop ao vivo",
    "cmd_login_desc": "logar o Shimpz no Claude Code",
    "cmd_reset_desc": "reiniciar a conversa",
    "cmd_clear_desc": "apagar o histórico do chat",
    "cmd_start_desc": "iniciar",
    "cmd_lang_desc": "escolher pt/en para este chat",
    "status_thinking": "🤔 pensando…",
    "status_working": "⏳ trabalhando",
    "heartbeat_suffix": "{} · ainda nisso…",
    "continuing_banner": "▶️ Continuando de onde parei…",
    "checkpointing": "🧠 resumindo o contexto…",
    "stale_button": "botão expirado",
    "word_yes": "sim",
    "word_no": "não",
    "word_on": "ligada",
    "word_off": "desligada",
    "voice_auto_hint": "auto (quando você manda áudio)",
    "voice_auto": "auto",
    "voice_always": "sempre",
    "voice_never": "nunca",
    "login_res_logged_in_as": "logado como {}",
    "login_res_logged_in": "logado",
    "login_res_cli_output": "{}",
    "login_res_failed_exit": "o login falhou (saída {})",
    "login_res_timeout": "o login não recebeu o código em {}s e expirou",
    "login_res_error": "erro no login: {}",
    "login_res_ended_unexpectedly": "o login terminou de forma inesperada",
}


def t(key, *args, lang="en"):
    """Render `key` in `lang` ("pt"|"en"), `.format(*args)`'d if any were given.

    Stateless: the caller resolves the language (from LANG_PREF / SHIMPZ_UI_LANG) and passes it, so this
    leaf keeps no state.
    """
    tmpl = (_UI_PT if lang == "pt" else _UI_EN)[key]
    return tmpl.format(*args) if args else tmpl


def status_for(tool, inp, lang="en"):
    """One short 'what I'm doing right now' line for the status ticker."""
    cmd = ""
    if isinstance(inp, dict):
        cmd = inp.get("command") or inp.get("file_path") or inp.get("url") or inp.get("description") or ""
    blob = ((tool or "") + " " + str(cmd)).lower()
    if "shimpz-shot" in blob or "screenshot" in blob or "import -window" in blob:
        return t("status_looking", lang=lang)
    if "shimpz-input type" in blob or "shimpz-input key" in blob:
        return t("status_typing", lang=lang)
    if "shimpz-input" in blob or "xdotool" in blob:
        return t("status_mouse", lang=lang)
    if "shimpz-tg" in blob:
        return t("status_notifying", lang=lang)
    if "shimpz-approve" in blob or "shimpz-captcha" in blob:
        return t("status_need_you", lang=lang)
    name = (tool or "").lower()
    if name in ("webfetch", "websearch") or "webread" in blob or "curl" in blob:
        return t("status_searching", lang=lang)
    if name in ("write", "edit", "notebookedit"):
        return t("status_editing", lang=lang)
    if name in ("read", "grep", "glob"):
        return t("status_reading", lang=lang)
    if name == "bash":
        return t("status_running", lang=lang)
    return t("status_tool_fallback", tool or "working", lang=lang)
