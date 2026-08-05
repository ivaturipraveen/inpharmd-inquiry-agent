import { FC, useEffect, useRef, useState } from "react";

interface Props {
  onEdit: () => void;
  onDelete: () => void;
}

const RowMenu: FC<Props> = ({ onEdit, onDelete }) => {
  const [open, setOpen] = useState(false);
  const [anchor, setAnchor] = useState<{ top: number; right: number } | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const triggerRef = useRef<HTMLButtonElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    const esc = (e: KeyboardEvent) => e.key === "Escape" && setOpen(false);
    document.addEventListener("mousedown", close);
    document.addEventListener("keydown", esc);
    return () => {
      document.removeEventListener("mousedown", close);
      document.removeEventListener("keydown", esc);
    };
  }, [open]);

  return (
    <div className="row-menu" ref={ref}>
      <button
        ref={triggerRef}
        type="button"
        className={`menu-trigger ${open ? "menu-trigger-open" : ""}`}
        onClick={(e) => {
          e.stopPropagation();
          if (!open && triggerRef.current) {
            const rect = triggerRef.current.getBoundingClientRect();
            setAnchor({ top: rect.bottom + 4, right: window.innerWidth - rect.right });
          }
          setOpen((o) => !o);
        }}
        aria-label="Row actions"
        aria-haspopup="menu"
        aria-expanded={open}
      >
        <svg viewBox="0 0 16 16" fill="currentColor">
          <circle cx="8" cy="3" r="1.5" />
          <circle cx="8" cy="8" r="1.5" />
          <circle cx="8" cy="13" r="1.5" />
        </svg>
      </button>

      {open && anchor && (
        <div
          className="menu-popover menu-popover-fixed"
          style={{ top: anchor.top, right: anchor.right }}
          role="menu"
        >
          <button
            type="button"
            role="menuitem"
            className="menu-item"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
              onEdit();
            }}
          >
            <svg
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M14.5 2.5a2.1 2.1 0 0 1 3 3L7 16l-4 1 1-4Z" />
            </svg>
            Edit
          </button>
          <button
            type="button"
            role="menuitem"
            className="menu-item menu-item-danger"
            onClick={(e) => {
              e.stopPropagation();
              setOpen(false);
              onDelete();
            }}
          >
            <svg
              viewBox="0 0 20 20"
              fill="none"
              stroke="currentColor"
              strokeWidth="1.6"
              strokeLinecap="round"
              strokeLinejoin="round"
            >
              <path d="M4 6h12M8 6V4h4v2M6 6l1 10h6l1-10" />
            </svg>
            Delete
          </button>
        </div>
      )}
    </div>
  );
};

export default RowMenu;
