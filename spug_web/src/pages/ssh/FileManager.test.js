import FileManager from './FileManager';
import {http} from 'libs';

jest.mock('libs', () => ({http: {put: jest.fn(), post: jest.fn()}, t: x => x, X_TOKEN: 'fixture', uniqueId: () => 'client-token'}));
jest.mock('components', () => ({AuthButton: () => null, Action: {Button: () => null}}));
jest.mock('components/ResizableTable', () => () => null);

test('upload obtains an authorized server token before opening progress or posting the file', async () => {
  const manager = new FileManager({id: 7});
  manager.setState = update => {manager.state = {...manager.state, ...update};};
  manager.input = {click: jest.fn(), value: 'file'};
  manager._updatePercent = jest.fn();
  manager.fetchFiles = jest.fn();
  http.put.mockResolvedValue('server-issued-token');
  http.post.mockResolvedValue({});
  manager.handleUpload();
  const file = new File(['fixture'], 'a.txt');
  await manager.input.onchange({target: {files: [file]}});
  expect(http.put).toHaveBeenCalledWith('/api/file/object/', {id: 7});
  expect(manager._updatePercent).toHaveBeenCalledWith('server-issued-token');
  expect(http.post.mock.calls[0][1].get('token')).toBe('server-issued-token');
  expect(http.post.mock.calls[0][1].get('id')).toBe('7');
});
