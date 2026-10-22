import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  useState,
  type ButtonHTMLAttributes,
  type ReactNode,
} from "react";
import { createPortal } from "react-dom";
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

/** Where an open menu goes, in viewport pixels (position: fixed). */
type MenuPlace = { top: number; left: number; side: "below" | "above" };

const MENU_GAP = 4; // between the button and the menu
const MENU_EDGE = 8; // kept free at the window's edges

/** Below the button, right edges lined up; above it if there's no room below; always inside the window. */
export function placeMenu(button: DOMRect, menu: { width: number; height: number }, view: { width: number; height: number }): MenuPlace {
  let side: MenuPlace["side"] = "below";
  let top = button.bottom + MENU_GAP;
  if (top + menu.height > view.height - MENU_EDGE) {
    const above = button.top - MENU_GAP - menu.height;
    if (above >= MENU_EDGE) {
      side = "above";
      top = above;
    } else {
      top = Math.max(MENU_EDGE, view.height - MENU_EDGE - menu.height); // fits neither way: as low as it can be
    }
  }
  const left = Math.min(Math.max(button.right - menu.width, MENU_EDGE), Math.max(MENU_EDGE, view.width - MENU_EDGE - menu.width));
  return { top, left, side };
}

/** Is any part of the element visible, inside the window and every area around it that cuts content off? */
function inSight(element: HTMLElement, view: { width: number; height: number }): boolean {
  const rect = element.getBoundingClientRect();
  const outside = (box: { top: number; bottom: number; left: number; right: number }) =>
    rect.bottom < box.top || rect.top > box.bottom || rect.right < box.left || rect.left > box.right;
  if (outside({ top: 0, left: 0, bottom: view.height, right: view.width })) return false;
  for (let parent = element.parentElement; parent && parent !== document.body; parent = parent.parentElement) {
    const style = getComputedStyle(parent);
    if (/(auto|scroll|hidden|clip)/.test(style.overflowX + style.overflowY) && outside(parent.getBoundingClientRect())) return false;
  }
  return true;
}

/** A "more actions" menu. Arrow keys move between items, Escape closes it and returns focus.
 *
 * The open menu is rendered outside the page's layout (a portal into the body, or into the open
 * <dialog> the button is in) and placed with position: fixed next to its button. Scroll areas such
 * as the accounts table (overflow-x: auto, which also clips vertically) can therefore never cut it
 * off or grow a scrollbar for it. It follows its button when anything scrolls or the window
 * changes size, and closes when the button scrolls out of sight. */
export function Menu({ label, items, icon }: { label: string; items: MenuItem[]; icon: LucideIcon }) {
  const [open, setOpen] = useState(false);
  const [place, setPlace] = useState<MenuPlace | null>(null);
  const button = useRef<HTMLButtonElement>(null);
  const list = useRef<HTMLDivElement>(null);
  const menuId = useId();

  const reposition = useCallback(() => {
    const trigger = button.current;
    const menu = list.current;
    if (!trigger || !menu) return;
    const view = { width: document.documentElement.clientWidth || window.innerWidth, height: window.innerHeight };
    if (!inSight(trigger, view)) {
      setOpen(false); // its button scrolled out of sight (of the window, or of the table's scroll area)
      return;
    }
    setPlace(placeMenu(trigger.getBoundingClientRect(), { width: menu.offsetWidth, height: menu.offsetHeight }, view));
  }, []);

  useLayoutEffect(() => {
    if (!open) {
      setPlace(null);
      return;
    }
    reposition();
    let frame = 0;
    const later = () => {
      cancelAnimationFrame(frame);
      frame = requestAnimationFrame(reposition);
    };
    window.addEventListener("scroll", later, true); // true: also scrolling inside the table or a drawer
    window.addEventListener("resize", later);
    return () => {
      cancelAnimationFrame(frame);
      window.removeEventListener("scroll", later, true);
      window.removeEventListener("resize", later);
    };
  }, [open, reposition]);

  const focusItem = (index: number) => {
    const entries = list.current?.querySelectorAll<HTMLButtonElement>('[role="menuitem"]:not([disabled])');
    if (!entries?.length) return;
    entries[(index + entries.length) % entries.length].focus();
  };

  // The first item gets focus once the menu is placed (it is hidden, so not focusable, until then).
  const focused = useRef(false);
  useEffect(() => {
    if (!open) focused.current = false;
    else if (place && !focused.current) {
      focused.current = true;
      focusItem(0);
    }
  }, [open, place]);

  useEffect(() => {
    if (!open) return;
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
      // The menu sits outside the page's order, so Tab goes on from the button, as if from the menu.
      setOpen(false);
      button.current?.focus();
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
      {open
        ? createPortal(
        <div
          className="menu"
          role="menu"
          id={menuId}
          aria-label={label}
          ref={list}
          onKeyDown={onKeyDown}
          data-side={place?.side}
          style={place ? { top: place.top, left: place.left } : { top: 0, left: 0, visibility: "hidden" }}
        >
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
        </div>,
        button.current?.closest("dialog[open]") ?? document.body,
      )
        : null}
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
