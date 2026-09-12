/**
 * ArtifactsView 组件测试
 *
 * 验证列表渲染、交付报告分区、plan_id 过滤、内容预览与未入库内容的降级提示。
 */
import { render, screen, waitFor } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ArtifactsView } from './ArtifactsView';
import { apiClient } from '../api/client';
import type { Artifact } from '../types';

vi.mock('../api/client', () => ({
  apiClient: {
    getArtifacts: vi.fn(),
    getArtifactContent: vi.fn(),
  },
}));

const mockedApi = vi.mocked(apiClient, true);

function makeArtifact(overrides: Partial<Artifact> & Pick<Artifact, 'artifact_id'>): Artifact {
  return {
    plan_id: 'plan-aaaa',
    run_id: null,
    step_id: null,
    kind: 'file',
    uri: 'artifact://demo',
    content_hash: null,
    size_bytes: 24,
    metadata: {},
    created_at: '2026-09-12T10:00:00',
    ...overrides,
  };
}

beforeEach(() => {
  vi.clearAllMocks();
});

describe('ArtifactsView', () => {
  it('renders grouped artifacts with delivery report section', async () => {
    mockedApi.getArtifacts.mockResolvedValue([
      makeArtifact({ artifact_id: 'file-1', kind: 'file', uri: 'file://out/report.md' }),
      makeArtifact({ artifact_id: 'report-1', kind: 'delivery_report', uri: 'artifact://report-1' }),
    ]);

    render(<ArtifactsView />);

    await waitFor(() => {
      expect(screen.getAllByText('交付报告').length).toBeGreaterThanOrEqual(2);
      expect(screen.getByText('步骤产物')).toBeInTheDocument();
    });
    expect(screen.getByText(/out\/report\.md/)).toBeInTheDocument();
  });

  it('shows empty state when no artifacts exist', async () => {
    mockedApi.getArtifacts.mockResolvedValue([]);
    render(<ArtifactsView />);
    await waitFor(() => {
      expect(screen.getByText(/还没有产物/)).toBeInTheDocument();
    });
  });

  it('previews artifact content on click', async () => {
    const user = userEvent.setup();
    mockedApi.getArtifacts.mockResolvedValue([
      makeArtifact({ artifact_id: 'report-1', kind: 'delivery_report', uri: 'artifact://report-1' }),
    ]);
    mockedApi.getArtifactContent.mockResolvedValue('# 交付报告\n全部完成。');

    render(<ArtifactsView />);
    await waitFor(() => expect(screen.getAllByText(/report-1/).length).toBeGreaterThan(0));

    await user.click(screen.getAllByText(/report-1/)[0]);
    await waitFor(() => {
      expect(screen.getByRole('dialog')).toBeInTheDocument();
      expect(screen.getByText(/全部完成/)).toBeInTheDocument();
      expect(mockedApi.getArtifactContent).toHaveBeenCalledWith('report-1');
    });
  });

  it('filters by plan_id', async () => {
    const user = userEvent.setup();
    mockedApi.getArtifacts.mockResolvedValue([]);
    render(<ArtifactsView />);

    await user.type(screen.getByLabelText('按 plan_id 过滤产物'), 'plan-1234');

    await waitFor(() => {
      expect(mockedApi.getArtifacts).toHaveBeenLastCalledWith('plan-1234');
    });
  });

  it('shows error banner when listing fails', async () => {
    mockedApi.getArtifacts.mockRejectedValue(new Error('服务不可用'));
    render(<ArtifactsView />);
    await waitFor(() => {
      expect(screen.getByRole('alert')).toHaveTextContent(/服务不可用/);
    });
  });
});
