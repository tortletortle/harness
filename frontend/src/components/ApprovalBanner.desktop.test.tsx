import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ApprovalBanner } from './ApprovalBanner';
import { useAppStore } from '../stores/appStore';
import { apiClient } from '../api/client';
import type { ApprovalRequestEvent } from '../types';
vi.mock('../api/client', () => ({ apiClient: { approveToolCall: vi.fn(), rejectToolCall: vi.fn(), getPendingApprovals: vi.fn() } }));
const request: ApprovalRequestEvent = { type:'approval_request',approval_id:'desktop-1',plan_id:'p',step_id:'s',tool_id:'desktop_observe',instruction:'test',inputs:{window_id:'123'},created_at:'2026-09-13',desktop_scope:{operation:'screenshot',summary:'截图不自动脱敏',confirmation:'digest',identity:{window_id:'123',pid:7,process_created:'42'},output:'C:/work/new.png'}};
beforeEach(() => { vi.clearAllMocks();useAppStore.setState({pendingApproval:request});vi.mocked(apiClient.getPendingApprovals).mockRejectedValue(new Error('offline')); });
describe('Desktop approval', () => {
  it('requires the explicit scope checkbox and sends confirmation',async()=>{
    render(<ApprovalBanner/>);expect(screen.getByTestId('approval-approve')).toBeDisabled();
    expect(screen.getByText(/窗口 HWND/)).toHaveTextContent('123');
    await userEvent.click(screen.getByTestId('desktop-confirm'));
    await userEvent.click(screen.getByTestId('approval-approve'));
    await waitFor(()=>expect(apiClient.approveToolCall).toHaveBeenCalledWith('p','desktop-1',undefined,'digest'));
  });
  it('reject requires no checkbox',async()=>{
    render(<ApprovalBanner/>);await userEvent.click(screen.getByTestId('approval-reject'));
    await waitFor(()=>expect(apiClient.rejectToolCall).toHaveBeenCalledWith('p','desktop-1',undefined));
  });
  it('does not transfer consent to another approval',async()=>{
    render(<ApprovalBanner/>);await userEvent.click(screen.getByTestId('desktop-confirm'));
    act(()=>useAppStore.setState({pendingApproval:{...request,approval_id:'desktop-2'}}));
    expect(screen.getByTestId('approval-approve')).toBeDisabled();expect(screen.getByTestId('desktop-confirm')).not.toBeChecked();
  });
  it('old payload without trusted scope cannot be approved',()=>{
    useAppStore.setState({pendingApproval:{...request,desktop_scope:undefined}});render(<ApprovalBanner/>);
    expect(screen.getByTestId('approval-approve')).toBeDisabled();
  });
  it('restores a pending request after reconnect/reload',async()=>{
    useAppStore.setState({pendingApproval:null});vi.mocked(apiClient.getPendingApprovals).mockResolvedValue([request]);
    render(<ApprovalBanner/>);await waitFor(()=>expect(screen.getByTestId('desktop-approval-scope')).toBeInTheDocument());
  });
});
