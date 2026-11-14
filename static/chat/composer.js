// composer feature: composes its submodules and exposes one interface.
import { createInput } from './composer/input.js';
import { createSlash } from './composer/slash.js';
import { createAttachments } from './composer/attachments.js';
import { createDrafts } from './composer/drafts.js';
import { createLibrary } from './composer/library.js';

export function createComposer(ctx) {
  const input = createInput({ ...ctx,

  });
  const library = createLibrary({ ...ctx,
    input,
    stash: () => slash.stashPrompt(),
  });
  const slash = createSlash({ ...ctx,
    input,
    library,
  });
  const attachments = createAttachments({ ...ctx,
    input,
  });
  const drafts = createDrafts({ ...ctx,
    input,
  });
  return {
    ...input, ...slash, ...attachments, ...drafts, ...library,
    get dockSpaceLocked() { return input.dockSpaceLocked; },
  };
}
