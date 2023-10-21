#!/usr/bin/env python3

import os
import protocaas.sdk as pr

try:
    from typing import List
    import json
    import shutil
    import subprocess
    import requests
except ImportError:
    # Do not raise import error if we are only generating the spec
    if os.environ.get('PROTOCAAS_GENERATE_SPEC', None) != '1':
        raise


app = pr.App(
    'dandi_upload', 
    help="Upload files to DANDI",
    app_image="magland/pc-dandi-upload",
    app_executable="/app/main.py"
)

description = """
Upload files to a dandiset on DANDI.
"""

@pr.processor('dandi_upload', help=description)
@pr.attribute('wip', True)
@pr.attribute('label', 'DANDI upload')
@pr.tags(['dandi'])
@pr.input_list('inputs', help='List of files to upload')
@pr.parameter('dandiset_id', type=str, help='Dandiset ID')
@pr.parameter('dandi_instance', default='dandi', type=str, help='dandi or dandi-staging')
@pr.parameter('dandi_api_key', secret=True, type=str, help='DANDI API key')
@pr.parameter('names', type=List[str], help='Destination names in the dandiset')
@pr.parameter('was_generated_by_jsons', type=List[str], help='The JSON strings containing the wasGeneratedBy metadata for each input file')
def dandi_upload(
    inputs: List[pr.InputFile],
    dandiset_id: str,
    dandi_instance: str,
    dandi_api_key: str,
    names: List[str],
    was_generated_by_jsons: List[str]
):
    print('Starting dandi_upload')

    if len(inputs) != len(names):
        raise Exception('Number of inputs does not match number of names')
    if len(inputs) == 0:
        raise Exception('No inputs')
    if not dandiset_id:
        raise Exception('dandiset_id is required')
    if not dandi_instance:
        raise Exception('dandi_instance is required')
    if not dandi_api_key:
        raise Exception('dandi_api_key is required')
    
    if dandi_instance == 'dandi':
        dandi_archive_url = 'https://dandiarchive.org'
    elif dandi_instance == 'dandi-staging':
        dandi_archive_url = 'https://gui-staging.dandiarchive.org'
    else:
        raise Exception(f'Unexpected dandi_instance: {dandi_instance}')
    
    dandiset_version = 'draft' # always going to be draft for uploading

    try:
        cmd = f'dandi download --dandi-instance {dandi_instance} --download dandiset.yaml {dandi_archive_url}/dandiset/{dandiset_id}/{dandiset_version}'
        print(f'Running command: {cmd}')
        env = {**os.environ, 'DANDI_API_KEY': dandi_api_key}
        result = subprocess.run(cmd, shell=True, env=env)
        if result.returncode != 0:
            raise Exception(f'Error running dandi download: {result.stderr}')

        workdir = dandiset_id

        for ii, inp in enumerate(inputs):
            name = names[ii]
            _make_sure_path_is_relative_and_is_safe(name)
            dest_path = os.path.join(workdir, name)
            # just to be extra safe, make sure dest_path is truly a subpath of workdir
            if not os.path.abspath(dest_path).startswith(os.path.abspath(workdir)):
                raise Exception(f'Unexpected error: dest_path is not a subpath of workdir: {dest_path}')
            print(f'Downloading input file {ii + 1} of {len(inputs)} to {name}')
            # make sure parent directories of dest_path exist
            os.makedirs(os.path.dirname(dest_path), exist_ok=True)
            inp.download(dest_path)
        
            print('Uploading file to DANDI')
            # skip validation for now, but we'll want to support this later
            cmd = f'dandi upload --dandi-instance {dandi_instance} --validation skip'
            print('Running command: ' + cmd)
            result = subprocess.run(cmd, shell=True, env=env, cwd=workdir)
            if result.returncode != 0:
                raise Exception(f'Error running dandi upload: {result.stderr}')
            
            # set the wasGeneratedBy metadata
            _set_was_generated_by(
                file_path=name,
                was_generated_by_json=was_generated_by_jsons[ii],
                staging=dandi_instance == 'dandi-staging',
                dandiset_id=dandiset_id,
                dandiset_version=dandiset_version,
                dandi_api_key=dandi_api_key
            )
            
            # remove the file
            os.remove(dest_path)
    finally:
        shutil.rmtree(workdir)

def _set_was_generated_by(
    file_path: str,
    was_generated_by_json: str,
    staging: bool,
    dandiset_id: str,
    dandiset_version: str,
    dandi_api_key: str
):
    headers = {
        'Authorization': f'token {dandi_api_key}'
    }
    assets_base_url = f'https://api{"-staging" if staging else ""}.dandiarchive.org/api/dandisets/{dandiset_id}/versions/{dandiset_version}/assets'
    assets_url = f'{assets_base_url}/?path={file_path}' 

    # Get the asset from the dandi api   
    res = requests.get(assets_url, headers=headers)
    if res.status_code != 200:
        print(res.status_code)
        print(res.json())
        raise Exception('Failed to get assets')
    assets = res.json()['results']
    if len(assets) == 0:
        print('Asset not found')
        return
    if len(assets) > 1:
        print('More than one asset found')
    asset = assets[0]

    # Get the asset metadata from the dandi api
    asset_url = f'{assets_base_url}/{asset["asset_id"]}/'
    res = requests.get(asset_url, headers=headers)
    if res.status_code != 200:
        print(res.status_code)
        print(res.json())
        raise Exception('Failed to get metadata for asset')
    metadata = res.json()

    # Add the wasGeneratedBy metadata
    x = metadata['wasGeneratedBy']
    x.append(json.loads(was_generated_by_json))

    # Replace the asset with a new asset with the updated metadata
    put_json = {
        "blob_id": asset["blob"],
        "metadata": metadata
    }
    res = requests.put(asset_url, headers=headers, json=put_json)
    if res.status_code != 200:
        print(res.status_code)
        print(res.json())
        raise Exception('Failed to update metadata for asset')
    

def _make_sure_path_is_relative_and_is_safe(path):
    if path.startswith('/'):
        raise Exception('Path cannot start with /')
    components = path.split('/')
    for comp in components:
        if comp == '.':
            raise Exception('Path cannot contain .')
        if comp == '..':
            raise Exception('Path cannot contain ..')
        if comp == '':
            raise Exception('Path cannot contain empty component')

app.add_processor(dandi_upload)

if __name__ == '__main__':
    app.run()