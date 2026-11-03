"use client";
import { Button, Modal } from "./ui";
import type { SetupState } from "./SetupWizard";

/** Settings -> Health. For now: setup steps that were skipped or left incomplete (item 2 adds the live checks). */
export function Health({ setup, onClose, onOpenSetup }: { setup: SetupState | null; onClose: () => void; onOpenSetup: () => void }) {
  const warnings = setup?.warnings ?? [];
  return (
    <Modal title="Health" onClose={onClose}>
      {warnings.length === 0 ? (
        <p className="text-[13px] text-mute">No setup warnings.</p>
      ) : (
        <ul className="divide-y divide-line border-y border-line">
          {warnings.map((w) => (
            <li key={w.step} className="flex items-baseline gap-3 py-2 text-[13px]">
              <span className="font-mono text-accent" aria-label="warning">!</span>
              <span className="w-44 shrink-0 text-fg">{w.label}</span>
              <span className="min-w-0 flex-1 text-dim">{w.note}</span>
            </li>
          ))}
        </ul>
      )}
      <div className="mt-4 flex justify-end">
        <Button onClick={onOpenSetup}>{setup?.finished ? "Run setup again" : "Open setup"}</Button>
      </div>
    </Modal>
  );
}
