async function loadResolveSources(client) {
  const [chatsResult, contactsResult] = await Promise.allSettled([
    client.getChats(),
    client.getContacts(),
  ]);

  if (chatsResult.status === 'rejected' && contactsResult.status === 'rejected') {
    throw chatsResult.reason || contactsResult.reason;
  }

  return {
    chats: chatsResult.status === 'fulfilled' ? chatsResult.value : [],
    contacts: contactsResult.status === 'fulfilled' ? contactsResult.value : [],
  };
}

function normalizedCandidateName(value) {
  return (value || '')
    .toString()
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLocaleLowerCase('es')
    .trim();
}

function identityKind(id) {
  const value = (id || '').toString().toLowerCase();
  if (value.endsWith('@c.us')) return 'phone';
  if (value.endsWith('@lid')) return 'lid';
  return 'other';
}

function dedupeLinkedIdentityCandidates(candidates) {
  const result = [];
  for (const candidate of candidates) {
    const name = normalizedCandidateName(candidate.name);
    const kind = identityKind(candidate.id);
    const duplicateIndex = result.findIndex(existing => {
      const existingKind = identityKind(existing.id);
      return (
        !candidate.isGroup &&
        !existing.isGroup &&
        candidate.score === existing.score &&
        name &&
        name === normalizedCandidateName(existing.name) &&
        new Set([kind, existingKind]).size === 2 &&
        [kind, existingKind].every(value => value === 'phone' || value === 'lid')
      );
    });
    if (duplicateIndex < 0) {
      result.push(candidate);
    } else if (kind === 'phone') {
      result[duplicateIndex] = candidate;
    }
  }
  return result;
}

module.exports = { loadResolveSources, dedupeLinkedIdentityCandidates };
