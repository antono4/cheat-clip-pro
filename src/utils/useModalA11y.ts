import { useEffect, useRef } from 'react';

const FOCUSABLE_SELECTOR = [
  'a[href]',
  'button:not([disabled])',
  'textarea:not([disabled])',
  'input:not([disabled]):not([type="hidden"])',
  'select:not([disabled])',
  '[tabindex]:not([tabindex="-1"])',
].join(', ');

function isVisible(el: HTMLElement): boolean {
  if (el.getClientRects().length === 0) return false;
  const style = window.getComputedStyle(el);
  return style.visibility !== 'hidden' && style.display !== 'none';
}

/**
 * Gives a hand-rolled modal the keyboard and screen-reader behaviour a native
 * <dialog> would provide: Escape closes it, Tab cycles inside it instead of
 * escaping to the page behind, focus enters on open and returns to whatever
 * opened it on close, and the container is announced as a modal dialog.
 *
 * Attach the returned ref to the modal's outermost element and give that
 * element `tabIndex={-1}` so it can receive focus when it holds no controls.
 * The hook must be called unconditionally, before any early return.
 */
export function useModalA11y(isOpen: boolean, onClose: () => void, label?: string) {
  const ref = useRef<HTMLDivElement>(null);
  const previouslyFocused = useRef<HTMLElement | null>(null);
  const onCloseRef = useRef(onClose);

  // Keep the latest onClose reachable from the keydown listener without making
  // it an effect dependency (callers pass a fresh closure every render, which
  // would re-run the effect and re-steal focus).
  useEffect(() => {
    onCloseRef.current = onClose;
  }, [onClose]);

  useEffect(() => {
    const node = ref.current;
    if (!isOpen || !node) return;

    // Remember the opener only when focus is still outside the dialog, so a
    // re-render while open cannot overwrite it with a node inside the modal.
    if (!node.contains(document.activeElement)) {
      previouslyFocused.current = document.activeElement as HTMLElement | null;
    }

    node.setAttribute('role', 'dialog');
    node.setAttribute('aria-modal', 'true');
    if (label) node.setAttribute('aria-label', label);

    const focusable = () =>
      Array.from(node.querySelectorAll<HTMLElement>(FOCUSABLE_SELECTOR)).filter(isVisible);

    (focusable()[0] ?? node).focus({ preventScroll: true });

    const handleKeyDown = (event: KeyboardEvent) => {
      if (event.key === 'Escape') {
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== 'Tab') return;

      const items = focusable();
      if (items.length === 0) return;
      const first = items[0];
      const last = items[items.length - 1];

      if (event.shiftKey && document.activeElement === first) {
        event.preventDefault();
        last.focus();
      } else if (!event.shiftKey && document.activeElement === last) {
        event.preventDefault();
        first.focus();
      }
    };

    document.addEventListener('keydown', handleKeyDown, true);
    return () => document.removeEventListener('keydown', handleKeyDown, true);
  }, [isOpen, label]);

  // Restore focus only on a real open→closed transition, never from the
  // cleanup of the effect above (which also runs on unrelated re-renders).
  useEffect(() => {
    if (isOpen) return;
    const opener = previouslyFocused.current;
    previouslyFocused.current = null;
    if (opener && document.contains(opener)) opener.focus({ preventScroll: true });
  }, [isOpen]);

  // Modals whose parent unmounts them on close (rather than toggling `isOpen`)
  // never see the transition above, so restore focus when the node goes away.
  useEffect(
    () => () => {
      const opener = previouslyFocused.current;
      previouslyFocused.current = null;
      if (opener && document.contains(opener)) opener.focus({ preventScroll: true });
    },
    [],
  );

  return ref;
}
