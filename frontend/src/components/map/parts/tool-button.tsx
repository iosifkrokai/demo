import type { ReactNode } from 'react';

interface ToolButtonProps {
  title: string;
  icon: ReactNode;
  onClick: () => void;
  className?: string;
  disabled?: boolean;
  active?: boolean;
  'data-testid'?: string;
}

export function ToolButton({
  title,
  icon,
  onClick,
  className,
  disabled = false,
  active = false,
  'data-testid': testId,
}: ToolButtonProps) {
  return (
    <button
      type="button"
      aria-label={title}
      title={title}
      onClick={onClick}
      disabled={disabled}
      data-testid={testId}
      className={className}
      style={{
        width: '42px',
        height: '42px',
        backgroundColor: active ? '#e0f2fe' : '#ffffff',
        borderRadius: '4px',
        boxShadow: active
          ? '0 0 0 2px #3b82f6'
          : '0 0 0 2px rgba(0,0,0,0.1)',
        border: 'none',
        cursor: disabled ? 'default' : 'pointer',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        padding: 0,
        opacity: disabled ? 0.4 : 1,
      }}
    >
      <span aria-hidden={true} style={{ display: 'flex' }}>
        {icon}
      </span>
    </button>
  );
}
