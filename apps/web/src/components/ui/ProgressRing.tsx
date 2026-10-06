/** Circular gauge. Pure SVG; the arc animates by stroke-dashoffset. */
import { cn } from '@/lib/cn';

export interface ProgressRingProps {
  /** 0..1 */
  readonly value: number;
  readonly size?: number;
  readonly stroke?: number;
  readonly className?: string;
  /** Tailwind stroke class for the arc, e.g. `stroke-accent`. */
  readonly arcClassName?: string;
  readonly children?: React.ReactNode;
  readonly label: string;
}

export function ProgressRing({
  value,
  size = 64,
  stroke = 6,
  className,
  arcClassName = 'stroke-accent',
  children,
  label,
}: ProgressRingProps): JSX.Element {
  const clamped = Math.min(1, Math.max(0, value));
  const radius = (size - stroke) / 2;
  const circumference = 2 * Math.PI * radius;
  return (
    <div
      role="meter"
      aria-label={label}
      aria-valuemin={0}
      aria-valuemax={100}
      aria-valuenow={Math.round(clamped * 100)}
      className={cn('relative inline-flex shrink-0 items-center justify-center', className)}
      style={{ width: size, height: size }}
    >
      <svg width={size} height={size} className="-rotate-90" aria-hidden>
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          className="stroke-edge"
        />
        <circle
          cx={size / 2}
          cy={size / 2}
          r={radius}
          fill="none"
          strokeWidth={stroke}
          strokeLinecap="round"
          strokeDasharray={circumference}
          strokeDashoffset={circumference * (1 - clamped)}
          className={cn('transition-[stroke-dashoffset] duration-700 ease-out', arcClassName)}
        />
      </svg>
      <div className="absolute inset-0 flex items-center justify-center">{children}</div>
    </div>
  );
}
