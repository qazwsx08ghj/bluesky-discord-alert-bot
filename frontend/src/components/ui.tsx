import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { CircleAlert, CircleCheck, Info, TriangleAlert, X, type LucideIcon } from "lucide-react";
import { avatarColor, initials } from "../lib/people";

export const ICON = { size: 16, strokeWidth: 1.75, className: "icon", "aria-hidden": true } as const;
export const ICON_LG = { size: 20, strokeWidth: 1.75, className: "icon lg", "aria-hidden": true } as const;

// -- buttons ---------------------------------------------------------------------------------------
type Variant = "primary" | "danger" | "ghost" | "default";

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: Variant;
  size?: "sm" | "md";
  icon?: LucideIcon;
  busy?: boolean;
  /** Text read out while busy, e.g. "Saving…". */
  busyLabel?: string;
}

export function Button({ variant = "default", size = "md", icon: Icon, busy, busyLabel, children, className, disabled, ...rest }: ButtonProps) {
  const classes = ["btn", variant !== "default" && variant, size === "sm" && "sm", className].filter(Boolean).join(" ");
  return (
    <button type="button" className={classes} disabled={disabled || busy} aria-busy={busy || undefined} {...rest}>
      {busy ? <span className="spin" aria-hidden="true" /> : Icon ? <Icon {...ICON} /> : null}
      {busy && busyLabel ? busyLabel : children}
    </button>
  );
}

export function IconButton({ icon: Icon, label, size = "sm", variant = "ghost", ...rest }: Omit<ButtonProps, "icon" | "children"> & { icon: LucideIcon; label: string }) {
  return (
    <button
      type="button"
      className={["btn", variant !== "default" && variant, "icon-only", size === "sm" ? "sm" : "md"].filter(Boolean).join(" ")}
      aria-label={label}
      title={label}
      {...rest}
    >
      <Icon {...ICON} />
    </button>
  );
}

// -- small pieces -----------------------------------------------------------------------------------
export type Tone = "ok" | "warn" | "bad" | "idle" | "busy";

export function Badge({ tone, children }: { tone: Tone; children: ReactNode }) {
  return <span className={`badge ${tone}`}>{children}</span>;
}

export function Dot({ tone }: { tone: "ok" | "warn" | "bad" | "idle" }) {
  return <span className={`dot ${tone === "ok" ? "" : tone}`} aria-hidden="true" />;
}

export function Avatar({ name, handle, src, size }: { name: string; handle: string; src?: string | null; size?: number }) {
  const [failed, setFailed] = useState(false);
  const style = { background: avatarColor(handle), ...(size ? { width: size, height: size } : {}) };
  return (
    <div className="av" style={style} aria-hidden="true">
      {src && !failed && src.startsWith("https://") ? (
        <img src={src} alt="" referrerPolicy="no-referrer" onError={() => setFailed(true)} />
      ) : (
        initials(name)
      )}
    </div>
  );
}

export function Banner({
  tone,
  title,
  children,
  actions,
  icon,
}: {
  tone: "warn" | "bad" | "info";
  title: ReactNode;
  children?: ReactNode;
  actions?: ReactNode;
  icon?: LucideIcon;
}) {
  const Icon = icon ?? (tone === "info" ? Info : tone === "warn" ? TriangleAlert : CircleAlert);
  return (
    <div className={`banner ${tone}`} role={tone === "info" ? "status" : "alert"}>
      <Icon {...ICON_LG} />
      <div>
        <b className="banner-title">{title}</b>
        {children ? <p>{children}</p> : null}
      </div>
      {actions ? <div className="banner-act">{actions}</div> : null}
    </div>
  );
}

export function Empty({ icon: Icon, title, children, action, tone }: { icon: LucideIcon; title: string; children?: ReactNode; action?: ReactNode; tone?: "bad" }) {
  return (
    <div className={`empty ${tone ?? ""}`}>
      <div className="em-ic">
        <Icon {...ICON_LG} />
      </div>
      <h3>{title}</h3>
      {children ? <p>{children}</p> : null}
      {action}
    </div>
  );
}

export function FieldError({ id, children }: { id?: string; children: ReactNode }) {
  if (!children) return null;
  return (
    <div className="err-msg" id={id} role="alert">
      <CircleAlert {...ICON} />
      <span>{children}</span>
    </div>
  );
}

export function FieldOk({ children }: { children: ReactNode }) {
  return (
    <div className="ok-msg" role="status">
      <CircleCheck {...ICON} />
      <span>{children}</span>
    </div>
  );
}

export function FieldWarn({ children }: { children: ReactNode }) {
  return (
    <div className="warn-msg" role="status">
      <TriangleAlert {...ICON} />
      <span>{children}</span>
    </div>
  );
}

// -- dialogs ---------------------------------------------------------------------------------------
/** A native <dialog>, opened as a modal: focus stays inside, the rest of the page can't be
 * reached, and Escape closes it. Focus goes back to whatever opened it. */
export function Dialog({
  open,
  onClose,
  labelledBy,
  describedBy,
  className,
  role,
  children,
}: {
  open: boolean;
  onClose: () => void;
  labelledBy: string;
  describedBy?: string;
  className: string;
  role?: "dialog" | "alertdialog";
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const opener = useRef<Element | null>(null);
  const close = useRef(onClose);
  close.current = onClose;

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    if (open && !dialog.open) {
      opener.current = document.activeElement;
      dialog.showModal();
      // The browser would focus the first control (often Close); start where the form starts.
      dialog.querySelector<HTMLElement>("[data-autofocus]")?.focus();
    } else if (!open && dialog.open) {
      dialog.close();
    }
  }, [open]);

  useEffect(() => {
    const dialog = ref.current;
    if (!dialog) return;
    const onCancel = (event: Event) => {
      event.preventDefault(); // the parent decides; it may be busy saving
      close.current();
    };
    const onClosed = () => {
      const target = opener.current as HTMLElement | null;
      if (target && typeof target.focus === "function" && document.contains(target)) target.focus();
    };
    dialog.addEventListener("cancel", onCancel);
    dialog.addEventListener("close", onClosed);
    return () => {
      dialog.removeEventListener("cancel", onCancel);
      dialog.removeEventListener("close", onClosed);
    };
  }, []);

  return (
    <dialog
      ref={ref}
      className={className}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      role={role}
      onClick={(event) => {
        // A click on the backdrop (outside the box) closes it, like the Close button.
        if (event.target === ref.current) {
          const box = ref.current.getBoundingClientRect();
          const inside = event.clientX >= box.left && event.clientX <= box.right && event.clientY >= box.top && event.clientY <= box.bottom;
          if (!inside) close.current();
        }
      }}
    >
      {open ? children : null}
    </dialog>
  );
}

export function Confirm({
  open,
  title,
  children,
  confirmLabel,
  busy,
  onConfirm,
  onCancel,
  tone = "danger",
}: {
  open: boolean;
  title: string;
  children: ReactNode;
  confirmLabel: string;
  busy?: boolean;
  onConfirm: () => void;
  onCancel: () => void;
  tone?: "danger" | "primary";
}) {
  const id = useId();
  return (
    <Dialog open={open} onClose={busy ? () => undefined : onCancel} labelledBy={`${id}-t`} describedBy={`${id}-d`} className="modal" role="alertdialog">
      <h2 id={`${id}-t`}>{title}</h2>
      <div className="lead" id={`${id}-d`}>
        {children}
      </div>
      <div className="f">
        <Button onClick={onCancel} disabled={busy} data-autofocus>
          Cancel
        </Button>
        <Button variant={tone} onClick={onConfirm} busy={busy}>
          {confirmLabel}
        </Button>
      </div>
    </Dialog>
  );
}

// -- menu ------------------------------------------------------------------------------------------
export interface MenuItem {
  label: string;
  icon: LucideIcon;
  onSelect: () => void;
  danger?: boolean;
  disabled?: boolean;
}

/** A "more actions" menu. Arrow keys move between items, Escape closes it and returns focus. */
export function Menu({ label, items, icon }: { label: string; items: MenuItem[]; icon: LucideIcon }) {
  const [open, setOpen] = useState(false);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const focusItem = (index: number) => {
    const entries = list.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])');
    if (!entries?.length) return;
    entries[(index + entries.length) % entries.length].focus();
  };

  useEffect(() => {
    if (!open) return;
    focusItem(0);
    const onPointer = (event: PointerEvent) => {
      if (!list.current?.contains(event.target as Node) && !button.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener("pointerdown", onPointer);
    return () => document.removeEventListener("pointerdown", onPointer);
  }, [open]);

  const onKeyDown = (event: React.KeyboardEvent) => {
    const entries = Array.from(list.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])') ?? []);
    const index = entries.indexOf(document.activeElement as HTMLButtonElement);
    if (event.key === "ArrowDown") {
      event.preventDefault();
      focusItem(index + 1);
    } else if (event.key === "ArrowUp") {
      event.preventDefault();
      focusItem(index - 1);
    } else if (event.key === "Home") {
      event.preventDefault();
      focusItem(0);
    } else if (event.key === "End") {
      event.preventDefault();
      focusItem(-1);
    } else if (event.key === "Escape") {
      event.preventDefault();
      setOpen(false);
      button.current?.focus();
    } else if (event.key === "Tab") {
      setOpen(false);
    }
  };

  const Icon = icon;
  return (
    <>
      <button
        ref={button}
        type="button"
        className="btn sm ghost icon-only"
        aria-label={label}
        title={label}
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen((v) => !v)}
        onKeyDown={(event) => {
          if (event.key === "ArrowDown" && !open) {
            event.preventDefault();
            setOpen(true);
          }
        }}
      >
        <Icon {...ICON} />
      </button>
      {open ? (
        <div className="menu" role="menu" id={menuId} aria-label={label} ref={list} onKeyDown={onKeyDown}>
          {items.map((item) => (
            <button
              key={item.label}
              type="button"
              role="menuitem"
              className={item.danger ? "del" : undefined}
              disabled={item.disabled}
              tabIndex={-1}
              onClick={() => {
                setOpen(false);
                button.current?.focus();
                item.onSelect();
              }}
            >
              <item.icon {...ICON} />
              {item.label}
            </button>
          ))}
        </div>
      ) : null}
    </>
  );
}

// -- toasts ----------------------------------------------------------------------------------------
interface Toast {
  id: number;
  message: string;
  tone: "ok" | "bad";
}

const ToastContext = createContext<(message: string, tone?: "ok" | "bad") => void>(() => undefined);

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toasts, setToasts] = useState<Toast[]>([]);
  const next = useRef(1);
  const dismiss = useCallback((id: number) => setToasts((all) => all.filter((t) => t.id !== id)), []);
  const show = useCallback(
    (message: string, tone: "ok" | "bad" = "ok") => {
      const id = next.current++;
      setToasts((all) => [...all.slice(-2), { id, message, tone }]);
      window.setTimeout(() => dismiss(id), tone === "bad" ? 8000 : 4500);
    },
    [dismiss],
  );
  const item = (t: Toast) => (
    <div key={t.id} className={`toast ${t.tone === "bad" ? "bad" : ""}`}>
      {t.tone === "bad" ? <CircleAlert {...ICON} /> : <CircleCheck {...ICON} />}
      <span>{t.message}</span>
      <button type="button" aria-label="Dismiss message" onClick={() => dismiss(t.id)}>
        <X size={14} strokeWidth={2} aria-hidden="true" />
      </button>
    </div>
  );
  return (
    <ToastContext.Provider value={show}>
      {children}
      <div className="toasts">
        <div role="status" aria-live="polite" className="toast-col">
          {toasts.filter((t) => t.tone === "ok").map(item)}
        </div>
        <div role="alert" aria-live="assertive" className="toast-col">
          {toasts.filter((t) => t.tone === "bad").map(item)}
        </div>
      </div>
    </ToastContext.Provider>
  );
}

export const useToast = () => useContext(ToastContext);

// -- loading ---------------------------------------------------------------------------------------
export function SkeletonRows({ columns, rows = 3, label }: { columns: number; rows?: number; label: string }) {
  return (
    <>
      {Array.from({ length: rows }, (_, r) => (
        <tr key={r} className="loading-row" aria-hidden={r > 0 ? true : undefined}>
          {Array.from({ length: columns }, (_, c) => (
            <td key={c}>
              {r === 0 && c === 0 ? <span className="sr">{label}</span> : null}
              <span className="skel" style={{ width: c === 0 ? "70%" : "50%" }} />
            </td>
          ))}
        </tr>
      ))}
    </>
  );
}

export function SkeletonLines({ lines = 3, label }: { lines?: number; label: string }) {
  return (
    <div style={{ padding: 16, display: "grid", gap: 12 }} role="status">
      <span className="sr">{label}</span>
      {Array.from({ length: lines }, (_, i) => (
        <span key={i} className="skel" style={{ width: `${85 - i * 15}%` }} />
      ))}
    </div>
  );
}
