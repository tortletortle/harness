import { afterEach, describe, expect, it, vi } from 'vitest';
import { apiClient } from './client';
afterEach(()=>vi.unstubAllGlobals());
describe('Authenticated PNG content',()=>{
  it('requests an encoded artifact endpoint and returns the blob',async()=>{
    const blob=new Blob(['PNG'],{type:'image/png'});const fetch=vi.fn().mockResolvedValue({ok:true,status:200,headers:new Headers({'Content-Type':'image/png'}),blob:async()=>blob});vi.stubGlobal('fetch',fetch);
    expect(await apiClient.getArtifactImage('image?x')).toBe(blob);
    expect(fetch.mock.calls[0][0]).toContain('image%3Fx/content');
  });
  it('rejects SVG instead of embedding active markup',async()=>{
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,status:200,headers:new Headers({'Content-Type':'image/svg+xml'})}));
    await expect(apiClient.getArtifactImage('image')).rejects.toThrow('PNG');
  });
  it('rejects oversized images',async()=>{
    vi.stubGlobal('fetch',vi.fn().mockResolvedValue({ok:true,status:200,headers:new Headers({'Content-Type':'image/png'}),blob:async()=>new Blob([new Uint8Array(8*1024*1024+1)],{type:'image/png'})}));
    await expect(apiClient.getArtifactImage('image')).rejects.toThrow('大小限制');
  });
});
