import { beforeEach, describe, expect, it, vi } from 'vitest';
import { render, screen, waitFor, act } from '@testing-library/react';
import userEvent from '@testing-library/user-event';
import { ArtifactsView } from './ArtifactsView';
import { apiClient } from '../api/client';
vi.mock('../api/client', () => ({apiClient:{getArtifacts:vi.fn(),getArtifactContent:vi.fn(),getArtifactImage:vi.fn()}}));
const image={artifact_id:'screenshot',plan_id:'p',run_id:null,step_id:'s',kind:'desktop_screenshot',uri:'artifact://desktop-image',created_at:'2026-09-13',content_hash:'sha256:test',size_bytes:68};
beforeEach(()=>{
  vi.clearAllMocks();vi.mocked(apiClient.getArtifacts).mockResolvedValue([image]);
  vi.mocked(apiClient.getArtifactImage).mockResolvedValue(new Blob(['png'],{type:'image/png'}));
  Object.defineProperty(URL,'createObjectURL',{value:vi.fn(()=> 'blob:synthetic-image'),configurable:true});
  Object.defineProperty(URL,'revokeObjectURL',{value:vi.fn(),configurable:true});
});
describe('Screenshot previews',()=>{
  it('shows image rather than binary text and releases it on close',async()=>{
    render(<ArtifactsView/>);await userEvent.click(await screen.findByText('desktop-image'));
    expect(await screen.findByRole('img',{name:'窗口截图'})).toHaveAttribute('src','blob:synthetic-image');
    expect(apiClient.getArtifactContent).not.toHaveBeenCalled();
    expect(screen.getByText(/未自动遮挡隐私/)).toBeInTheDocument();
    await userEvent.click(screen.getByRole('button',{name:'关闭'}));
    expect(URL.revokeObjectURL).toHaveBeenCalledWith('blob:synthetic-image');
  });
  it('releases the image when leaving the page',async()=>{
    const view=render(<ArtifactsView/>);await userEvent.click(await screen.findByText('desktop-image'));
    await screen.findByRole('img');view.unmount();expect(URL.revokeObjectURL).toHaveBeenCalled();
  });
  it('ignores late responses after unmount',async()=>{
    let resolve!:(b:Blob)=>void;vi.mocked(apiClient.getArtifactImage).mockReturnValue(new Promise(r=>{resolve=r;}));
    const view=render(<ArtifactsView/>);await userEvent.click(await screen.findByText('desktop-image'));view.unmount();
    await act(async()=>{resolve(new Blob(['png']));});expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
  it('does not show an image when the server rejects its integrity',async()=>{
    vi.mocked(apiClient.getArtifactImage).mockRejectedValue(new Error('产物内容校验失败'));
    render(<ArtifactsView/>);await userEvent.click(await screen.findByText('desktop-image'));
    await waitFor(()=>expect(apiClient.getArtifactImage).toHaveBeenCalled());
    expect(screen.queryByRole('img')).not.toBeInTheDocument();expect(URL.createObjectURL).not.toHaveBeenCalled();
  });
});
