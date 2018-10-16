
import threading
import datetime
import imutils
import cv2
import numpy as np
import time
import math
import os
import sys 
import traceback

from PyQt4 import QtGui,QtCore
from PIL import Image
from PIL import ImageTk
import hough_transform as ht
import skvideo.io as skv

# device imports

import device_init
from pymba import *
from my_andor.andor_wrap import *
from ctypes import *
import zaber.serial as zs

# graphing imports
import matplotlib
matplotlib.use('TkAgg')
from matplotlib.backends.backend_qt4agg import FigureCanvasQTAgg
from matplotlib.figure import Figure
import matplotlib.pyplot as plt
from scipy.optimize import curve_fit
from scipy.interpolate import griddata
from mpl_toolkits.mplot3d import Axes3D
from matplotlib import cm


class EMCCDthread(QtCore.QThread):

    def __init__(self,app):
        super(EMCCDthread,self).__init__()

        self.app = app
        self.andor = app.andor
        self.motor = app.motor
        self.graph = app.graph
        self.stop_event = app.stop_event
        self.andor_lock = app.andor_lock
        self.condition = app.condition

        self.PlasticBS =  9.6051
        self.WaterBS = 5.1157

        self.image = None
        self.image_andor = None
        self.analyzed_row = np.zeros(80)

        self.export_list = []
        self.brillouin_shift_list = []
        self.scan_ready = False

    def get_water_BS(self,T):
        return -5.219*10**(-5)*T**2 + 0.01312*T + 4.836

    def get_plastic_BS(self,T):
        return 16.3291 - (-0.0005512*T**2 + 0.04333*T + 6.019)

    """
    This function is only used as a subfunction in scan(), returns the next frame from the EMCCD camera
    and the calculated Brillouin shift value from that frame
    """
    def acquire_frame(self):
        with self.andor_lock:
            self.andor.cam.StartAcquisition() 
            data = []                                            
            self.andor.cam.GetAcquiredData(data)

        image_array = np.array(data, dtype = np.uint16)  
        maximum = image_array.max()
        proper_image = np.reshape(image_array, (-1, 512))
        scaled_image = proper_image*(255.0/maximum)
        scaled_image = scaled_image.astype(int)
        scaled_8bit = np.array(scaled_image, dtype = np.uint8)

        loc = np.argmax(scaled_8bit)/512
        left_right = scaled_8bit[loc].argsort()[-10:][::-1]
        left_right.sort()
        mid = int((left_right[0]+left_right[-1])/2)

        cropped = scaled_8bit[loc:loc+1, mid-40:mid+40] # only returns the middle strip showing Brillouin peaks

        if cropped.size == 0:
            print "CROPPED IS EMPTY"
            cropped = np.zeros((14,80), dtype = np.uint8)

        image = imutils.resize(cropped, width=1024)

        #obtain brillouin value
        FSR = float(self.app.FSR_entry.displayText())
        SD = float(self.app.SD_entry.displayText())

        graph_data = list(data)
        graph_array = np.array(graph_data, dtype = np.uint16)
        reshaped_graph = np.reshape(graph_array, (-1,512))

        analyzed_row = reshaped_graph[loc][mid-40:mid+40]

        x0_1 = np.argmax(analyzed_row[:40])
        x0_2 = np.argmax(analyzed_row[40:])+40
        delta_peaks = x0_2 - x0_1
        BS = (FSR - delta_peaks*SD)/2

        return image, BS


    def update_scanned_location(self, relative_coord, BS_profile, image_list, start_pos, length, num_steps):

        if self.app.CMOSthread.scan_loc is not None:
            #updating stored scanned locations
            loc_ID = self.app.get_current_ID()
            self.app.scanned_locations[loc_ID] = relative_coord
            self.app.brillouin_profiles[loc_ID] = np.array(BS_profile) #brillouin_profiles lists all scans ever taken
            self.app.spectrograph_dict[loc_ID] = np.array(image_list)

            average_shift = sum(list(map(lambda profile: profile[1],BS_profile)))/len(BS_profile)

            display_coord = (relative_coord[0],-relative_coord[1])

            table = self.app.scanned_loc_table
            current_row = table.rowCount()-1
            table.insertRow(current_row)
            table_elements = [display_coord,average_shift,start_pos,length,num_steps,loc_ID]
            table_items = list(map(lambda elt: QtGui.QTableWidgetItem(str(elt)),table_elements))
            for col_num in range(len(table_items)):
                table.setItem(current_row,col_num,table_items[col_num])
            
            self.app.CMOSthread.update_coord_panel() #update coord panel

    """
    Systematically calls acquire_frame() to get the next frame and BS value, then moves the motor, 
    then calls acquire_frame(), then moves the motor etc.. At the end, exports the all the frames and BS 
    values collected throughout the scan.
    """
    def scan(self, start_pos, length, num_steps):

        ############
        ### SCAN ###
        ############
        print "starting scan"
        self.app.move_motor_abs(start_pos)
        step_size = length // num_steps
        BS_profile = []
        image_list = []

        image,BS = self.acquire_frame()
        BS_profile.append((start_pos,BS))

        self.app.spectrograph.clear()

        image_list.append(image)

        image = Image.fromarray(image)
        self.export_list.append(image) #initial frame


        for i in range(1,num_steps+1):

            self.app.move_motor_rel(step_size)           
            
            image,BS = self.acquire_frame()
            BS_profile.append((start_pos+step_size*i,BS))

            image_list.append(image)
            
            image = Image.fromarray(image)
            self.export_list.append(image)
            print "processed frame"


        scan_loc = self.app.CMOSthread.scan_loc
        center = self.app.detected_center
        if scan_loc is not None and center is not None:
            relative_coord = (scan_loc[0]-center[0],scan_loc[1]-center[1])
            self.update_scanned_location(relative_coord,BS_profile,image_list,start_pos,length,num_steps)

            self.emit(QtCore.SIGNAL('update_heatmap_panel(PyQt_PyObject)'),(relative_coord,BS_profile))

        #EXPORT
        
        for image in image_list:
            image = cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)
            item = QtGui.QListWidgetItem()
            pixmap = self.app.convert_to_pixmap(image)
            icon = QtGui.QIcon()
            icon.addPixmap(pixmap)
            item.setIcon(icon)
            self.app.spectrograph.addItem(item)

    """
    Just like CMOSthread, this runs the EMCCDloop which involves acquiring frames from the EMCCD camera
    and also running graphloop (below)
    """
    def run(self):
        while not self.stop_event.is_set():
            with self.andor_lock:
                self.andor.cam.StartAcquisition() 
                data = []                                            
                self.andor.cam.GetAcquiredData(data)

            image_array = np.array(data, dtype = np.uint16)
            #print "andorLoop"

            maximum = image_array.max()

            proper_image = np.reshape(image_array, (-1, 512))

            scaled_image = proper_image*(255.0/maximum)
            scaled_image = scaled_image.astype(int)
            scaled_8bit = np.array(scaled_image, dtype = np.uint8)

            loc = np.argmax(scaled_8bit)/512
            left_right = scaled_8bit[loc].argsort()[-10:][::-1]
            left_right.sort()
            mid = int((left_right[0]+left_right[-1])/2)

            ################
            ### GRAPHING ###
            ################

            graph_data = list(data)
            graph_array = np.array(graph_data, dtype = np.uint16)
            reshaped_graph = np.reshape(graph_array, (-1,512))

            self.analyzed_row = reshaped_graph[loc][mid-40:mid+40]

            self.graphLoop()

            ###############

            cropped = scaled_8bit[loc-7:loc+7, mid-40:mid+40]

            (h, w)= cropped.shape[:2]
            if w <= 0 or h <= 0:
                continue

            self.image_andor = cropped

            image = imutils.resize(self.image_andor, width=1024)
            bgr_image = cv2.cvtColor(image,cv2.COLOR_GRAY2BGR)

            height, width, channel = bgr_image.shape
            bytesPerLine = 3 * width
            qImage = QtGui.QImage(bgr_image.data, width, height, bytesPerLine, QtGui.QImage.Format_RGB888) 

            self.emit(QtCore.SIGNAL('update_EMCCD_panel(PyQt_PyObject)'),qImage)


    """
    Takes frame taken from EMCCD camera and collects points that it will eventually plot to the scatterplot
    and the Brillouin plot. 
    """
    def graphLoop(self):

        copied_analyzed_row = np.array(self.analyzed_row)


        graph = self.app.graph
        graph.fig.clf()
        self.app.subplot = graph.fig.add_subplot(211)
        self.app.subplot.set_xlabel("Pixel")
        self.app.subplot.set_ylabel("Counts")
        self.app.subplot.scatter(graph.x_axis, copied_analyzed_row, s = 1)

        try:
            FSR = float(self.app.FSR_entry.displayText())
            SD = float(self.app.SD_entry.displayText())
            
            if not self.app.reference_btn.isChecked():
                constant_1 = np.amax(copied_analyzed_row[:40])
                constant_2 = np.amax(copied_analyzed_row[40:])
                x0_1 = np.argmax(copied_analyzed_row[:40])
                x0_2 = np.argmax(copied_analyzed_row[40:])+40
                gamma_1 = None
                gamma_2 = None
                for i in xrange(40 - x0_1): 
                    half_max = constant_1/2
                    if copied_analyzed_row[:40][x0_1+i] <= half_max:
                        gamma_1 = i*2
                        break
                for j in xrange(40 - (x0_2 - 40)):
                    half_max = constant_2/2
                    if copied_analyzed_row[40:][x0_2 - 40+j] <= half_max:
                        gamma_2 = j*2
                        break

                delta_peaks = x0_2 - x0_1
                BS = (FSR - delta_peaks*SD)/2
                length_bs = len(self.brillouin_shift_list)

                self.brillouin_shift_list.append(BS)
                if length_bs >= 20:
                    self.brillouin_shift_list = self.brillouin_shift_list[1:]
                length_bs +=1

                if gamma_1 is not None and gamma_2 is not None:
                    popt, pcov = curve_fit(lorentzian, self.graph.x_axis, copied_analyzed_row, p0 = np.array([gamma_1, x0_1, constant_1, gamma_2, x0_2, constant_2, 100]))
                
                    self.app.subplot.plot(self.app.graph.x_axis, lorentzian(self.app.graph.x_axis, *popt), 'r-', label='fit')

            else:
                constant_1 = np.amax(copied_analyzed_row[:20])
                constant_2 = np.amax(copied_analyzed_row[20:40])
                constant_3 = np.amax(copied_analyzed_row[40:60])
                constant_4 = np.amax(copied_analyzed_row[60:])
                constant_5 = 100


                x0_1 = np.argmax(copied_analyzed_row[:20])
                x0_2 = np.argmax(copied_analyzed_row[20:40])+20
                x0_3 = np.argmax(copied_analyzed_row[40:60])+40
                x0_4 = np.argmax(copied_analyzed_row[60:])+60

                temp = 23.5
                plastic_BS = self.get_plastic_BS(temp)
                water_BS = self.get_water_BS(temp)

                popt, pcov = curve_fit(lorentzian_reference, self.graph.x_axis, copied_analyzed_row, p0 = np.array([1, x0_1, constant_1, 1, x0_2, constant_2, 1, x0_3, constant_3, 1, x0_4, constant_4, constant_5]))

                self.app.subplot.plot(self.app.graph.x_axis, lorentzian_reference(self.app.graph.x_axis, *popt), 'r-', label='fit')

                measured_SD = (2*plastic_BS - 2*water_BS) / ((x0_4 - x0_1) + (x0_3 - x0_2))
                measured_FSR = 2*plastic_BS - measured_SD*(x0_3 - x0_2)

                self.app.SD_entry.setText(str(measured_SD))
                self.app.FSR_entry.setText(str(measured_FSR))

        except Exception as e:
            traceback.print_exc()
            pass

        self.app.brillouin_plot = graph.fig.add_subplot(212)
        self.app.brillouin_plot.set_xlabel("Brillouin Shift Frequency (GHz)")
        self.app.brillouin_plot.set_ylabel("Points")
        self.app.brillouin_plot.set_ylim(bottom=4.0,top=7.0)
        
        self.app.brillouin_plot.scatter(np.arange(1, len(self.brillouin_shift_list)+1), np.array(self.brillouin_shift_list))

        self.app.graph_panel.canvas.draw()


class Graph(object):
    def __init__(self):
        self.fig = Figure(figsize = (10, 10), dpi = 100, tight_layout = True)
        self.x_axis = np.arange(1,81)


class HeatMapGraph:
    def __init__(self,resolution,depth):
        self.fig = Figure(figsize = (10,10), dpi = 80)
        self.ax = None
        self.res = None
        self.depth = depth
        self.scanned_BS_values = {}

        self.set_resolution(resolution,-600,600,-600,600)

    def plot(self):

        points = self.scanned_BS_values.keys()
        if len(points) < 4:
            self.set_resolution(25,-600,600,-600,600)
            null_array = np.full(self.X.shape,-1)
            for point in points: 
                index_coord = ((-point[1])/self.res+24,point[0]/self.res+24)
                null_array[index_coord] = self.scanned_BS_values[point]

            masked_array = np.ma.array(null_array, mask=np.less(null_array,np.zeros(null_array.shape)))
        else:
            display_points = list(map(lambda point: (point[0],-point[1]),points))
            x_list = list(map(lambda point: point[0],display_points))
            y_list = list(map(lambda point: point[1],display_points))

            # AUTO-ADJUST RESOLUTION
            min_x, max_x, min_y, max_y = (min(x_list),max(x_list),min(y_list),max(y_list))
            data_range = max(max_x-min_x,max_y-min_y)
            if data_range/50 <= 1:
                resolution = 1
            else:
                resolution = min(data_range/50,50)

            self.set_resolution(resolution,min_x,max_x,min_y,max_y)

            heatmap_points = list(map(lambda point: (point[0]/self.res*self.res,point[1]/self.res*self.res),display_points))
            values = np.array(list(map(lambda point: self.scanned_BS_values[point],points))).reshape(-1)
            grid = griddata(heatmap_points,values,(self.X,self.Y),method="cubic")

            masked_array = np.ma.array(grid, mask=np.isnan(grid))
       

        # CREATE NEW SUBPLOT #
        
        self.fig.clf()

        self.ax = self.fig.add_subplot(111)
        colormap = cm.rainbow
        colormap.set_bad('white',1.)

        plot = self.ax.pcolormesh(self.X, self.Y, masked_array, cmap=cm.rainbow)
        map(lambda point: self.ax.text(point[0],-point[1],str(self.scanned_BS_values[point]),color='black',size='x-small'),points)

        cb = self.fig.colorbar(plot,fraction=0.046, pad=0.04)

        if self.depth == -1:
            self.fig.suptitle("Average Brillouin Shift Frequency Map (GHz)",  fontsize=12)
        else:
            self.fig.suptitle("Brillouin Shift Frequency Map at %f(GHz)" % self.depth,  fontsize=12)
        self.ax.set_xlabel('x (pixels)')
        self.ax.set_ylabel('y (pixels)')
        self.ax.set_aspect('equal')


    def set_resolution(self,resolution,min_x,max_x,min_y,max_y):
        self.res = resolution
        x_range = max_x-min_x
        y_range = max_y-min_y
        diff = math.ceil(abs((x_range - y_range)/2))
        if x_range > y_range:
            min_y -= diff
            max_y += diff
        elif y_range > x_range:
            min_x -= diff
            max_x += diff

        X = np.arange(min_x/self.res*self.res,math.ceil(max_x/self.res)*self.res+1,self.res)
        Y = np.arange(min_y/self.res*self.res,math.ceil(max_y/self.res)*self.res+1,self.res)
        self.X, self.Y = np.meshgrid(X,Y)



def lorentzian(x, gamma_1, x0_1, constant_1, gamma_2, x0_2, constant_2, constant_3):
    numerator_1 = 0.5*gamma_1*constant_1
    denominator_1 = math.pi * (x - x0_1) **2 + (0.5 * gamma_1)**2
    numerator_2 = 0.5*gamma_2*constant_2
    denominator_2 = math.pi * (x - x0_2) **2 + (0.5 * gamma_2)**2
    y = (numerator_1/denominator_1) + (numerator_2/denominator_2) + constant_3
    return y

def lorentzian_reference(x, gamma_1, x0_1, constant_1, gamma_2, x0_2, constant_2, gamma_3, x0_3, constant_3, gamma_4, x0_4, constant_4, constant_5):
    numerator_1 = 0.5*gamma_1*constant_1
    denominator_1 = math.pi * (x - x0_1) **2 + (0.5 * gamma_1)**2
    numerator_2 = 0.5*gamma_2*constant_2
    denominator_2 = math.pi * (x - x0_2) **2 + (0.5 * gamma_2)**2
    numerator_3 = 0.5*gamma_3*constant_3
    denominator_3 = math.pi * (x - x0_3) **2 + (0.5 * gamma_3)**2
    numerator_4 = 0.5*gamma_4*constant_4
    denominator_4 = math.pi * (x - x0_4) **2 + (0.5 * gamma_4)**2
    y = (numerator_1/denominator_1) + (numerator_2/denominator_2) + (numerator_3/denominator_3) + (numerator_4/denominator_4) + constant_5
    return y