package dev.ccpocket.daemon.codex

import kotlin.test.Test
import kotlin.test.assertEquals

class CodexLauncherTest {
    @Test
    fun normalModeUsesTheOriginalServerCommand() {
        assertEquals(listOf("app-server"), CodexLauncher.buildArgs(stableMode = false))
    }

    @Test
    fun stableModeDisablesDelegationWithoutDisablingTheRequiredExecutionHost() {
        assertEquals(
            listOf(
                "app-server",
                "--disable", "multi_agent",
                "--disable", "multi_agent_v2",
                "--enable", "shell_tool",
                "--enable", "unified_exec",
            ),
            CodexLauncher.buildArgs(stableMode = true),
        )
    }
}
