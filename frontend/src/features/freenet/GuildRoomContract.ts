/**
 * GuildRoomContract — typed interfaces and CRDT helpers for the GuildRoom contract.
 */

export interface GuildRoomMessage {
  message_id: string;
  author_id: number;
  author_name: string;
  content: string;
  timestamp: string;
  nostr_event_id?: string;
}

export interface GuildRoomMember {
  agent_id: number;
  agent_name: string;
  joined_at: string;
  npub?: string;
}

export interface GuildRoomState {
  room_id: string;
  guild_slug: string;
  messages: GuildRoomMessage[];
  members: GuildRoomMember[];
  version: number;
  last_updated: string;
}

/**
 * CRDT merge of two GuildRoomState values.
 *
 * Rules:
 *  - messages: union by message_id (first-seen wins for content)
 *  - members:  LWW (last-writer-wins) by joined_at timestamp per agent_id
 *  - version:  max of both
 *  - last_updated: whichever corresponds to the higher version
 */
export function mergeStates(local: GuildRoomState, remote: GuildRoomState): GuildRoomState {
  // --- Messages: union by message_id ---
  const msgMap = new Map<string, GuildRoomMessage>();
  for (const msg of local.messages) {
    msgMap.set(msg.message_id, msg);
  }
  for (const msg of remote.messages) {
    if (!msgMap.has(msg.message_id)) {
      msgMap.set(msg.message_id, msg);
    }
  }
  const messages = Array.from(msgMap.values()).sort((a, b) =>
    a.timestamp < b.timestamp ? -1 : a.timestamp > b.timestamp ? 1 : 0,
  );

  // --- Members: LWW by joined_at per agent_id ---
  const memberMap = new Map<number, GuildRoomMember>();
  for (const member of [...local.members, ...remote.members]) {
    const existing = memberMap.get(member.agent_id);
    if (!existing || member.joined_at > existing.joined_at) {
      memberMap.set(member.agent_id, member);
    }
  }
  const members = Array.from(memberMap.values());

  // --- Version & last_updated ---
  const version = Math.max(local.version, remote.version);
  const last_updated = local.version >= remote.version ? local.last_updated : remote.last_updated;

  return {
    room_id: local.room_id,
    guild_slug: local.guild_slug,
    messages,
    members,
    version,
    last_updated,
  };
}

/** Construct an empty GuildRoomState for a given room. */
export function emptyRoomState(roomId: string, guildSlug: string): GuildRoomState {
  return {
    room_id: roomId,
    guild_slug: guildSlug,
    messages: [],
    members: [],
    version: 0,
    last_updated: new Date().toISOString(),
  };
}
