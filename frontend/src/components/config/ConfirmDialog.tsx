import type { ReactNode } from 'react';
import { trapFocus } from './shared';

/** Small blocking confirmation used for discard and paid model tests. */
export function ConfirmDialog({
  id,
  icon,
  title,
  children,
  confirmLabel,
  cancelLabel,
  onConfirm,
  onCancel,
}: {
  id: string;
  icon: ReactNode;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  cancelLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}) {
  return (
    <div className="mc-dialog-backdrop">
      <div
        className="mc-dialog"
        role="alertdialog"
        aria-modal="true"
        aria-labelledby={`${id}-title`}
        aria-describedby={`${id}-desc`}
        onKeyDown={(event) => {
          if (event.key === 'Escape') {
            event.stopPropagation();
            onCancel();
            return;
          }
          trapFocus(event);
        }}
      >
        {icon}
        <h3 id={`${id}-title`}>{title}</h3>
        <div id={`${id}-desc`} className="mc-dialog-body">
          {children}
        </div>
        <div className="mc-dialog-actions">
          <button className="mc-button mc-secondary" autoFocus onClick={onCancel}>
            {cancelLabel}
          </button>
          <button className="mc-button mc-save" onClick={onConfirm}>
            {confirmLabel}
          </button>
        </div>
      </div>
    </div>
  );
}
