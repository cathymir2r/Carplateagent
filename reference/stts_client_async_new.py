#!/usr/bin/env python
# -*- coding: utf-8 -*-

import json
import requests
import time
import os
import urllib.parse
import random
import sys

# engine = "http://30.118.219.138"
# engine = "http://30.171.82.93:9875"

audio_file_path = "D:/data/tts/audio/"

def stts_async(engineUrl, audio_file_path, text, vid, sample_rate):
    url = engineUrl + "/api/v1/lark/tts/synthesizeAsync"

    # sessionId 参数用于标识一次请求，所以最好每个请求都不一样，方便查引擎的日志
    sessionId = "py_tts_test_20241206_02" + str(random.randint(1, 1000))

    result_type_aac = 3
    result_type_wav = 1
    result_type_pcm = 0

    audio_result_file = audio_file_path + "/" + sessionId + ".pcm"

    request_data = {
        "text": text,
        "vid": vid,
        "spd": 0,
        "vol": 0,
        "pit": 0,
        "smooth": 2,
        "sessionId": sessionId,
        "resType": result_type_pcm,
        "splitType": 1,
        "bizName": "ISPS",
        "sampleRate": sample_rate,
        "appId": "ISPS",
        "sceneId": "ISPS",
        "requireTimeline": 0
    }

    print("sending ", request_data["sessionId"])
    response = requests.post(url, json=request_data)

    responseCode = response.headers.get('responseCode')
    if responseCode != '200':
        print('合成失败！', response.headers.get('responseMsg'))
        exit(1)
    get_async_result(response, engineUrl, audio_result_file)


def get_async_result(response, engineUrl, audio_result_file):
    if os.path.exists(audio_result_file):
        os.remove(audio_result_file)
    url = engineUrl + "/api/v1/lark/tts/getSynthesizeResult"

    req = response.json()

    packageIndex = 0

    while True:
        # time.sleep(0.02)
        # req["packageIndex"] = packageIndex
        packageIndex = packageIndex + 1
        print("req text:", json.dumps(req))
        response = requests.post(url, json=req)
        percent = response.headers.get('percent')
        if percent:
            print("percent:", percent)
        contentLength = response.headers.get('TTS-ContentLength')
        if contentLength:
            print("contentLength:", contentLength)

        responseCode = response.headers.get('responseCode')
        print(responseCode)
        if responseCode == '200':
            audioBytes = response.content
            if audioBytes:
                save_audio_to_file(audioBytes, audio_result_file)
            break
        elif responseCode == '206':
            # time_line.append(response.headers.get('timeline'))
            audioBytes = response.content
            save_audio_to_file(audioBytes, audio_result_file)
            continue
        elif responseCode == '204':
            continue
        else:
            break

    print("finish")


def save_audio_to_file(audioBytes, audio_result_file):
    l = len(audioBytes)
    lms = l / 16
    print("write bytes:", l, "len ms", lms)
    with open(audio_result_file, 'ab') as f:
        f.write(audioBytes)


if __name__ == '__main__':
    engineUrl = sys.argv[1]
    audio_file_path = sys.argv[2]
    text = sys.argv[3]
    vid = sys.argv[4]
    sample_rate = sys.argv[5]

    # stts_async(engineUrl, audio_file_path, text, vid, sample_rate)

    stts_async("http://localhost:9876", "D:/data/tts/audio/", "你好，中国平安", "zsfb1034E", "8k")