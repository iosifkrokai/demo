import { useTranslation } from 'react-i18next';
import type { TFunction } from 'i18next';

import type {
  AgentRouteResponse,
  PlanRequirement,
  PlanRequirementReason,
} from '../types';

interface PlanVerdictProps {
  /** The agent's answer to the last /routes/generate; null before any ask. */
  response: AgentRouteResponse | null;
}

/** Known verdict reasons; unknown codes fall back to the generic label, not the raw key. */
const KNOWN_REASONS: readonly PlanRequirementReason[] = [
  'must_visit_on_route',
  'must_visit_absent',
  'must_visit_unroutable',
  'must_visit_outside_coverage',
  'service_on_route',
  'service_along_route',
  'hard_service_absent',
  'soft_service_absent',
  'service_not_measured',
  'interest_absent',
  'avoid_honoured',
  'avoid_violated',
  'route_missing',
  'geometry_missing',
];

/** The reason as a short label; the code itself is never shown to the tourist. */
const reasonLabel = (
  reason: PlanRequirementReason | null | undefined,
  t: TFunction
): string =>
  reason && KNOWN_REASONS.includes(reason)
    ? t(`sidebar.status.reason.${reason}`)
    : t('sidebar.status.reasonFallback');

/** What the requirement is called on screen: the name, else the code, else the kind. */
const requirementName = (item: PlanRequirement): string =>
  item.name || item.code || item.kind;

/** The agent's verdict: why the route could not be built or which request went unmet. */
export const PlanVerdict = ({ response }: PlanVerdictProps) => {
  const { t } = useTranslation();
  const status = response?.status;
  const unmet = response?.interpretation?.unmet ?? [];

  if (status !== 'infeasible' && status !== 'degraded') return null;

  const unmetList = (
    <ul className="mt-1 list-disc space-y-0.5 pl-4">
      {unmet.map((item, i) => (
        <li key={i}>
          {requirementName(item)} — {reasonLabel(item.reason, t)}
        </li>
      ))}
    </ul>
  );

  if (status === 'infeasible') {
    const outside = unmet.filter(
      (item) => item.reason === 'must_visit_outside_coverage'
    );
    if (outside.length > 0) {
      const names = outside.map(requirementName).filter(Boolean).join(', ');
      return (
        <div
          data-testid="plan-verdict"
          role="alert"
          className="rounded-xl bg-destructive/10 px-3 py-2 text-meta text-destructive"
        >
          <p>{t('sidebar.status.outsideCoverageTitle', { names })}</p>
          <p>{t('sidebar.status.outsideCoverageHint')}</p>
        </div>
      );
    }
    return (
      <div
        data-testid="plan-verdict"
        role="alert"
        className="rounded-xl bg-destructive/10 px-3 py-2 text-meta text-destructive"
      >
        <p>{t('sidebar.status.impossibleTitle')}</p>
        {unmetList}
      </div>
    );
  }

  return (
    <div
      data-testid="plan-verdict"
      role="status"
      className="rounded-xl bg-amber-500/15 px-3 py-2 text-meta text-amber-800"
    >
      <p>{t('sidebar.status.degradedTitle')}</p>
      {unmetList}
    </div>
  );
};
