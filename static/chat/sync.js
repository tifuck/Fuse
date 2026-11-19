// sync feature: composes its submodules and exposes one interface.
import { createStatus } from './sync/status.js';
import { createPersist } from './sync/persist.js';
import { createApply } from './sync/apply.js';
import { createStream } from './sync/stream.js';
import { createNotices } from './sync/notices.js';

export function createSync(ctx) {
  const notices = createNotices(ctx);
  const status = createStatus({ ...ctx,
    notices,
    apply: { reconcileThread: (...args) => apply.reconcileThread(...args) },
  });
  const persist = createPersist({ ...ctx,
    apply: { reconcileThread: (...args) => apply.reconcileThread(...args) },
    status,
  });
  const apply = createApply({ ...ctx,
    status,
    persist,
  });
  const stream = createStream({ ...ctx,
    apply,
    notices,
    persist,
    status,
  });
  return { ...status, ...persist, ...apply, ...stream };
}
