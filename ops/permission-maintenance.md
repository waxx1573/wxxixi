# pipi permission maintenance

Administrator identities are maintained in AstrBot native admins_id.
Current authorized accounts were narrowed on 2026-09-09 to the pipi bot identity
and the explicitly confirmed human account only; the former extra administrator
was revoked. For a new administrator, obtain UID using /sid in an enabled group
and add that UID under AstrBot configuration, Basic, Administrator ID. Nicknames,
group IDs and UMO strings are not administrator IDs. Verify after saving.

The WeChat
group manager uses event.is_admin() and no longer reads plugin-specific admin
lists or grants administrator access to bot_self_ids. Identity errors deny access.

Stealer 3.0.2 is deployed with the local patch in stealer-admin-only.patch. It checks
the unwrapped event identity before manual image capture via the steal_meme LLM tool.
Ordinary members may still chat, search and use read-only commands. Automatic capture
uses the existing group scope and review settings.

Before upgrading Stealer, check whether upstream provides equivalent checks. Reapply
or port the minimal patch only after checking the new code. After deployment, verify
the guard appears immediately after unwrap_event(event), before feature, scope,
image or network access. Verify both direct and wrapped tool calls reject non-admin
users before any file/network access, and an admin still reaches the existing feature
gate. Do not treat plugin startup as a permission test.

Run tests/test_admin_boundaries.py for the group-manager identity boundary.
Live rejection using a separate ordinary-member account remains to be verified.
