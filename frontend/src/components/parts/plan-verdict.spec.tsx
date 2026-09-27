import { describe, expect, it } from 'vitest';
import { render, screen } from '@testing-library/react';

import { PlanVerdict } from './plan-verdict';
import type {
  AgentRouteResponse,
  PlanInterpretation,
  PlanRequirement,
} from '../types';

/** A requirement the way the backend sends one; tests override what matters. */
const requirement = (over: Partial<PlanRequirement>): PlanRequirement => ({
  kind: 'must_visit',
  strength: 'hard',
  code: null,
  name: 'Кафедральный собор Святого Станислава',
  origin: 'agent',
  status: 'unmet',
  reason: 'must_visit_outside_coverage',
  place_ids: [],
  ...over,
});

const interpretation = (
  over: Partial<PlanInterpretation>
): PlanInterpretation => ({
  status: 'infeasible',
  requirements: [],
  unmet: [],
  unknowns: [],
  ...over,
});

const response = (over: Partial<AgentRouteResponse>): AgentRouteResponse => ({
  ...over,
});

describe('PlanVerdict', () => {
  it('молчит на ready — обычный случай не говорит ничего', () => {
    const { container } = render(
      <PlanVerdict
        response={response({
          status: 'ready',
          interpretation: interpretation({ status: 'ready' }),
        })}
      />
    );

    expect(screen.queryByTestId('plan-verdict')).not.toBeInTheDocument();
    expect(container).toBeEmptyDOMElement();
  });

  it('infeasible по outside_coverage называет имена и говорит, куда смотреть', () => {
    render(
      <PlanVerdict
        response={response({
          status: 'infeasible',
          interpretation: interpretation({
            unmet: [
              requirement({ name: 'Кафедральный собор Святого Станислава' }),
            ],
          }),
        })}
      />
    );

    const verdict = screen.getByTestId('plan-verdict');
    expect(verdict).toHaveTextContent(
      'Сюда маршрут не построить: Кафедральный собор Святого Станислава — вне зоны покрытия (Гродненская область)'
    );
    expect(verdict).toHaveTextContent('Попробуйте точку внутри области');
    // Имена печатаются как в данных — без перевода и без причины в этой строке.
    expect(verdict).not.toHaveTextContent('must_visit_outside_coverage');
  });

  it('infeasible по другой причине — общий заголовок и список unmet', () => {
    render(
      <PlanVerdict
        response={response({
          status: 'infeasible',
          interpretation: interpretation({
            unmet: [
              requirement({
                name: 'Замок на Немане',
                reason: 'must_visit_absent',
              }),
            ],
          }),
        })}
      />
    );

    const verdict = screen.getByTestId('plan-verdict');
    expect(verdict).toHaveTextContent(
      'Маршрут не построен: не удалось выполнить обязательное требование'
    );
    expect(verdict).toHaveTextContent('Замок на Немане');
    expect(verdict).toHaveTextContent('обязательная точка не найдена в данных');
  });

  it('degraded перечисляет оба unmet с подписями причин', () => {
    render(
      <PlanVerdict
        response={response({
          status: 'degraded',
          interpretation: interpretation({
            status: 'degraded',
            unmet: [
              requirement({
                name: 'Купеческий дом',
                kind: 'service',
                reason: 'service_along_route',
              }),
              requirement({
                name: 'Галерея Кухмистр',
                kind: 'interest',
                reason: 'interest_absent',
              }),
            ],
          }),
        })}
      />
    );

    const verdict = screen.getByTestId('plan-verdict');
    expect(verdict).toHaveTextContent('Часть запроса выполнить не удалось');
    expect(verdict).toHaveTextContent('Купеческий дом');
    expect(verdict).toHaveTextContent('удобство не рядом с маршрутом');
    expect(verdict).toHaveTextContent('Галерея Кухмистр');
    expect(verdict).toHaveTextContent('интерес не найден в данных');
  });

  it('пустой unmet при infeasible не роняет компонент — виден только заголовок', () => {
    render(
      <PlanVerdict
        response={response({
          status: 'infeasible',
          interpretation: interpretation({ unmet: [] }),
        })}
      />
    );

    const verdict = screen.getByTestId('plan-verdict');
    expect(verdict).toHaveTextContent(
      'Маршрут не построен: не удалось выполнить обязательное требование'
    );
    expect(verdict.querySelectorAll('li')).toHaveLength(0);
  });

  it('ответ без interpretation не роняет компонент', () => {
    const { container } = render(
      <PlanVerdict response={response({ status: 'infeasible' })} />
    );

    expect(screen.getByTestId('plan-verdict')).toHaveTextContent(
      'Маршрут не построен: не удалось выполнить обязательное требование'
    );
    expect(container).not.toBeEmptyDOMElement();
  });
});
